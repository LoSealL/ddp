import os
import json
import time
from datetime import datetime

print("=== DDP Sample Job ===")
print(f"Started at: {datetime.now()}")

for i in range(3):
    print(f"  Processing step {i + 1}/3...")
    time.sleep(0.5)

os.makedirs("output", exist_ok=True)

with open("output/result.txt", "w") as f:
    f.write(f"Job completed at {datetime.now()}\n")
    f.write("Steps processed: 3\n")

with open("output/metrics.json", "w") as f:
    json.dump(
        {
            "status": "success",
            "timestamp": datetime.now().isoformat(),
            "steps": 3,
            "duration_sec": 1.5,
        },
        f,
        indent=2,
    )

print("Done. Outputs written to output/")
