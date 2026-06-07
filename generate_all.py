"""Generate GTFS feeds for all CityBus.gr operators."""

import subprocess
import sys

CITIES = [
    "alexandroupoli", "arta", "chalkida", "chania", "chios", "corfu",
    "drama", "ioannina", "irakleio", "kastoria", "katerini", "kavala",
    "komotini", "kozani", "lamia", "larisa", "mitilini", "naousa",
    "patra", "ptolemaida", "salamina", "serres", "skiathos", "veroia",
    "volos", "xanthi",
]

if __name__ == "__main__":
    failed = []
    for city in CITIES:
        print(f"\n{'='*60}\n  {city.upper()}\n{'='*60}")
        result = subprocess.run([sys.executable, "citybus_gtfs.py", "--subdomain", city])
        if result.returncode != 0:
            failed.append(city)

    if failed:
        print(f"\nFailed ({len(failed)}): {', '.join(failed)}")
    else:
        print(f"\nAll {len(CITIES)} cities generated successfully.")
