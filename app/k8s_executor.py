import asyncio
import os
import secrets
from datetime import datetime, timezone

from kubernetes import client, config
from kubernetes.client.exceptions import ApiException

from . import db, gpu, images
from .storage import Storage

NAMESPACE = os.environ.get("DDP_K8S_NAMESPACE", "ddp")
RUNNER_IMAGE = os.environ.get("DDP_RUNNER_IMAGE", "172.16.50.3:5000/neospark/ddp-runner:latest")
S3_SECRET = "ddp-s3-creds"
BUCKET = "ddp"
PROXY = os.environ.get("DDP_POD_PROXY", "http://172.16.50.3:7891")

_GPU_NODE_LABEL = "acceleratable.feature.gpustack.ai/nvidia"

_S3_ENV = [client.V1EnvVar(k, value_from=client.V1EnvVarSource(
    secret_key_ref=client.V1SecretKeySelector(name=S3_SECRET, key=sk)))
    for k, sk in [("DDP_S3_ENDPOINT", "endpoint"), ("DDP_S3_ACCESS_KEY", "access_key"),
                  ("DDP_S3_SECRET_KEY", "secret_key"), ("DDP_S3_BUCKET", "bucket")]]


class K8sExecutor:
    """Two-phase job runner.

    prepare(): PVC + cpu-only ssh debug pod + NodePort svc (immediate)
    execute(): delete debug pod, create GPU batch Job on the same PVC
    watch():   poll GPU job, fetch logs, run output collector job
    """

    def __init__(self, storage: Storage):
        self.storage = storage
        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()
        self.batch = client.BatchV1Api()
        self.core = client.CoreV1Api()

    def _name(self, job_id: str) -> str:
        return f"ddp-{job_id}"

    def _labels(self, job: dict) -> dict:
        return {"app": "ddp", "ddp/job-id": job["id"], "ddp/user-id": str(job.get("user_id"))}

    def _user_pvc(self, job: dict) -> str:
        return f"ddp-user-{job.get('user_id')}"

    # ── phase 1: debug environment ──────────────

    async def prepare(self, job: dict) -> dict:
        """Create user PVC (if new) + debug pod + svc.

        Returns {'ssh_port': int, 'ssh_password': str}.
        Workspace is one PVC per user, shared and persisted across their jobs.
        """
        job_id = job["id"]
        name = self._name(job_id)
        password = secrets.token_urlsafe(9)
        labels = self._labels(job)
        pvc_name = f"ddp-user-{job.get('user_id')}"

        # RWO binds to one node forever: existing PVC -> stick to its node;
        # new PVC -> pick the node with the most free GPU memory.
        try:
            pvc_obj = await asyncio.to_thread(
                self.core.read_namespaced_persistent_volume_claim, pvc_name, NAMESPACE)
            node = (pvc_obj.metadata.annotations or {}).get("volume.kubernetes.io/selected-node")
            node_selector = ({"kubernetes.io/hostname": node} if node
                             else {_GPU_NODE_LABEL: "true"})
        except ApiException as e:
            if e.status != 404:
                raise
            node_selector = {_GPU_NODE_LABEL: "true"}
            try:
                best = max(gpu.fetch_gpu_status(), key=lambda g: g["mem_total"] - g["mem_used"],
                           default=None)
                if best:
                    node_selector = {"kubernetes.io/hostname": best["node"]}
            except Exception:
                pass
            pvc = client.V1PersistentVolumeClaim(
                metadata=client.V1ObjectMeta(name=pvc_name, namespace=NAMESPACE,
                                             labels={"app": "ddp", "ddp/user-id": str(job.get("user_id"))}),
                spec=client.V1PersistentVolumeClaimSpec(
                    access_modes=["ReadWriteOnce"],
                    resources=client.V1VolumeResourceRequirements(
                        requests={"storage": f"{job.get('storage_gb') or 10}Gi"})))
            await asyncio.to_thread(
                self.core.create_namespaced_persistent_volume_claim, NAMESPACE, pvc)
        pod = client.V1Pod(
            metadata=client.V1ObjectMeta(name=name, namespace=NAMESPACE, labels=labels),
            spec=client.V1PodSpec(
                restart_policy="Always",
                node_selector=node_selector,
                containers=[client.V1Container(
                    name="debug",
                    image=images.pull_spec(job["image"]),
                    env=[client.V1EnvVar("POD_MODE", "ssh"),
                         client.V1EnvVar("SSH_PASSWORD", password),
                         # toolkit mounts driver libs but zero devices -> nvidia-smi shows no GPUs
                         client.V1EnvVar("NVIDIA_VISIBLE_DEVICES", "void"),
                         client.V1EnvVar("HTTP_PROXY", PROXY),
                         client.V1EnvVar("HTTPS_PROXY", PROXY),
                         client.V1EnvVar("NO_PROXY", "localhost,127.0.0.1,172.16.0.0/16,10.0.0.0/8")],
                    ports=[client.V1ContainerPort(22)],
                    resources=client.V1ResourceRequirements(
                        requests={"cpu": str(job.get("cpu") or 2),
                                  "memory": f"{job.get('memory_gb') or 4}Gi"},
                        limits={"cpu": str(job.get("cpu") or 2),
                                "memory": f"{job.get('memory_gb') or 4}Gi"}),
                    volume_mounts=[client.V1VolumeMount(name="workspace", mount_path="/workspace")])],
                volumes=[client.V1Volume(
                    name="workspace",
                    persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(pvc_name))]))
        svc = client.V1Service(
            metadata=client.V1ObjectMeta(name=name, namespace=NAMESPACE, labels=labels),
            spec=client.V1ServiceSpec(
                type="NodePort",
                selector={"ddp/job-id": job_id},
                ports=[client.V1ServicePort(port=22, target_port=22)]))

        await asyncio.to_thread(self.core.create_namespaced_pod, NAMESPACE, pod)
        svc_obj = await asyncio.to_thread(self.core.create_namespaced_service, NAMESPACE, svc)
        return {"ssh_port": svc_obj.spec.ports[0].node_port, "ssh_password": password}

    async def wait_ready(self, job_id: str):
        """Flip initializing -> pending once the debug pod accepts ssh."""
        name = self._name(job_id)
        for _ in range(120):  # ~10min (image pull on first use)
            job = db.get_job(job_id)
            if not job or job["status"] != "initializing":
                return
            try:
                pod = await asyncio.to_thread(self.core.read_namespaced_pod, name, NAMESPACE)
                phase = pod.status.phase
                if phase == "Running" and pod.status.pod_ip:
                    try:
                        _, w = await asyncio.wait_for(
                            asyncio.open_connection(pod.status.pod_ip, 22), timeout=3)
                        w.close()
                        await w.wait_closed()
                        db.update_job(job_id, status="pending")
                        db.log_event("DEBUG", "system", f"Debug pod ready: {job_id}")
                        return
                    except (OSError, asyncio.TimeoutError):
                        pass
                elif phase in ("Failed", "Succeeded"):
                    db.update_job(job_id, status="failed",
                                  finished_at=db.now_iso(),
                                  error="debug pod exited before becoming ready")
                    return
            except ApiException as e:
                if e.status == 404:
                    return
                raise
            await asyncio.sleep(5)
        db.update_job(job_id, status="failed",
                      finished_at=db.now_iso(),
                      error="debug pod did not become ready in time")

    # ── phase 2: scheduled gpu run ──────────────

    def _gpu_job(self, job: dict) -> client.V1Job:
        job_id = job["id"]
        name = self._name(job_id)
        limits = {"cpu": str(job.get("cpu") or 2), "memory": f"{job.get('memory_gb') or 4}Gi"}
        if job.get("gpus"):
            limits["nvidia.com/gpu"] = job["gpus"]
            if job.get("gpu_mem_mb"):
                limits["nvidia.com/gpumem"] = job["gpu_mem_mb"]
        container = client.V1Container(
            name="runner",
            image=images.pull_spec(job["image"]),
            env=[client.V1EnvVar("POD_MODE", "run"),
                 client.V1EnvVar("SSH_PASSWORD", job.get("ssh_password") or "ddp123"),
                 client.V1EnvVar("ENTRY_COMMAND", job["entry_command"]),
                 client.V1EnvVar("HTTP_PROXY", PROXY),
                 client.V1EnvVar("HTTPS_PROXY", PROXY),
                 client.V1EnvVar("NO_PROXY", "localhost,127.0.0.1,172.16.0.0/16,10.0.0.0/8")],
            ports=[client.V1ContainerPort(22)],
            resources=client.V1ResourceRequirements(
                requests={"cpu": str(job.get("cpu") or 2),
                          "memory": f"{job.get('memory_gb') or 4}Gi"},
                limits=limits),
            volume_mounts=[client.V1VolumeMount(name="workspace", mount_path="/workspace")])
        pod_spec = client.V1PodSpec(
            restart_policy="Never",
            containers=[container],
            volumes=[client.V1Volume(
                name="workspace",
                persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(
                    self._user_pvc(job)))])
        return client.V1Job(
            metadata=client.V1ObjectMeta(name=name, namespace=NAMESPACE, labels=self._labels(job)),
            spec=client.V1JobSpec(
                backoff_limit=0,
                active_deadline_seconds=job["timeout_minutes"] * 60,
                ttl_seconds_after_finished=300,
                template=client.V1PodTemplateSpec(
                    metadata=client.V1ObjectMeta(labels=self._labels(job)),
                    spec=pod_spec)))

    async def execute(self, job_id: str):
        job = db.get_job(job_id)
        if not job:
            return
        db.update_job(job_id, status="running",
                      started_at=db.now_iso())
        db.log_event("DEBUG", "system", f"Job started (k8s): {job_id}")
        name = self._name(job_id)
        # debug pod makes way for the gpu job (same PVC can't serve both)
        await self._ignore_notfound(self.core.delete_namespaced_pod, name, NAMESPACE)
        try:
            await asyncio.to_thread(self.batch.create_namespaced_job, NAMESPACE, self._gpu_job(job))
        except ApiException as e:
            if e.status != 409:
                db.update_job(job_id, status="failed",
                              finished_at=db.now_iso(),
                              error=f"k8s create failed: {e.reason}")
                db.log_event("ERROR", "system", f"Job {job_id} create failed: {e.reason}")
                return
        await self.watch(job_id)

    async def watch(self, job_id: str):
        """Poll gpu job until terminal, collect logs+outputs, update DB."""
        try:
            await self._watch(job_id)
        except Exception as e:
            # never leave a job stuck in "running" because the watcher died
            job = db.get_job(job_id)
            if job and job["status"] == "running":
                db.update_job(job_id, status="failed",
                              finished_at=db.now_iso(), error=f"watcher error: {e}")
            db.log_event("ERROR", "system", f"Watcher died for {job_id}: {e}")

    async def _watch(self, job_id: str):
        name = self._name(job_id)
        while True:
            try:
                st = await asyncio.to_thread(self.batch.read_namespaced_job_status, name, NAMESPACE)
            except ApiException as e:
                if e.status == 404:
                    # job deleted out from under us (manual kubectl delete or
                    # ttl cleanup). Only mark failed if still running — the
                    # cancel path deletes the job too and sets 'cancelled'.
                    job = db.get_job(job_id)
                    if job and job["status"] == "running":
                        db.update_job(job_id, status="failed",
                                      finished_at=db.now_iso(),
                                      error="k8s job deleted externally")
                        db.log_event("WARNING", "system", f"Job {job_id} disappeared from cluster")
                    return
                raise
            status = st.status
            if status.succeeded:
                result, error = "done", None
                break
            if status.failed:
                cond = next((c for c in (status.conditions or []) if c.type == "Failed"), None)
                if cond and cond.reason == "DeadlineExceeded":
                    result, error = "timeout", "killed after max runtime"
                else:
                    result = "failed"
                    error = cond.message if cond else "job failed"
                break
            await asyncio.sleep(5)

        await self._collect_logs(job_id)
        await self._collect_outputs(job_id)
        objects = self.storage.list_objects(self.storage.bucket, f"jobs/{job_id}/output/")
        db.update_job(job_id, status=result,
                      finished_at=db.now_iso(),
                      output_count=len(objects),
                      s3_prefix=f"{BUCKET}/jobs/{job_id}/",
                      error=error)
        db.log_event("DEBUG", "system", f"Job finished: {job_id} status={result}")

    # ── cancel / cleanup ────────────────────────

    async def cancel(self, job_id: str) -> bool:
        """Delete a running gpu job. Returns True if something was deleted."""
        try:
            await asyncio.to_thread(
                self.batch.delete_namespaced_job, self._name(job_id), NAMESPACE,
                propagation_policy="Background")
            return True
        except ApiException as e:
            if e.status == 404:
                return False
            raise

    async def teardown_debug(self, job_id: str):
        """Remove debug pod + svc (pending-job cancellation)."""
        name = self._name(job_id)
        await self._ignore_notfound(self.core.delete_namespaced_pod, name, NAMESPACE)
        await self._ignore_notfound(self.core.delete_namespaced_service, name, NAMESPACE)

    async def cleanup(self, job_id: str):
        """Remove all remaining resources (record deletion).

        The user PVC survives — workspaces persist across jobs by design.
        """
        await self.teardown_debug(job_id)

    async def delete_user_workspace(self, user_id: int):
        """Drop a user's workspace PVC (account deletion)."""
        await self._ignore_notfound(
            self.core.delete_namespaced_persistent_volume_claim,
            f"ddp-user-{user_id}", NAMESPACE)

    # ── internals ───────────────────────────────

    async def _ignore_notfound(self, fn, *args):
        try:
            await asyncio.to_thread(fn, *args)
        except ApiException as e:
            if e.status != 404:
                raise

    async def _collect_logs(self, job_id: str):
        try:
            pods = await asyncio.to_thread(
                self.core.list_namespaced_pod, NAMESPACE,
                label_selector=f"ddp/job-id={job_id}")
            runner = [p for p in pods.items if p.metadata.name != self._name(job_id)]
            if not runner:
                return
            # _preload_content=False: the default path returns the *repr* of
            # the raw bytes (content starts with b'), not decoded text
            resp = await asyncio.to_thread(
                self.core.read_namespaced_pod_log, runner[0].metadata.name, NAMESPACE,
                _preload_content=False)
            self.storage.upload_bytes(f"jobs/{job_id}/logs/run.log",
                                      resp.data.decode("utf-8", errors="replace").encode())
        except Exception as e:
            db.log_event("WARNING", "system", f"Log collection failed for {job_id}: {e}")

    async def _collect_outputs(self, job_id: str):
        """Run a short collector job that mounts the workspace and uploads output/."""
        job_row = db.get_job(job_id) or {}
        pvc_name = self._user_pvc(job_row)
        name = f"{self._name(job_id)}-collect"
        container = client.V1Container(
            name="collect",
            image=RUNNER_IMAGE,
            env=[client.V1EnvVar("JOB_ID", job_id),
                 client.V1EnvVar("OUTPUT_PATH", job_row.get("output_path") or "output"),
                 *_S3_ENV],
            volume_mounts=[client.V1VolumeMount(name="workspace", mount_path="/workspace")])
        job = client.V1Job(
            metadata=client.V1ObjectMeta(name=name, namespace=NAMESPACE,
                                         labels=self._labels({"id": job_id})),
            spec=client.V1JobSpec(
                backoff_limit=1,
                active_deadline_seconds=600,
                ttl_seconds_after_finished=60,
                template=client.V1PodTemplateSpec(
                    metadata=client.V1ObjectMeta(labels=self._labels({"id": job_id})),
                    spec=client.V1PodSpec(
                        restart_policy="Never",
                        containers=[container],
                        volumes=[client.V1Volume(
                            name="workspace",
                            persistent_volume_claim=client.V1PersistentVolumeClaimVolumeSource(
                                pvc_name))]))))
        try:
            await asyncio.to_thread(self.batch.create_namespaced_job, NAMESPACE, job)
            for _ in range(120):  # ponytail: 10min cap matches activeDeadlineSeconds
                st = await asyncio.to_thread(self.batch.read_namespaced_job_status, name, NAMESPACE)
                if st.status.succeeded or st.status.failed:
                    if st.status.failed:
                        db.log_event("WARNING", "system", f"Output collection failed for {job_id}")
                    return
                await asyncio.sleep(5)
        except Exception as e:
            db.log_event("WARNING", "system", f"Output collection failed for {job_id}: {e}")
