# CityBus GTFS Generator

Generates [GTFS](https://gtfs.org/) feeds from the [CityBus.gr](https://citybus.gr) REST API, covering 26 Greek urban bus operators.

## API Documentation

Interactive API docs: https://martinlangbecker.github.io/citybus-gtfs-generator/

## Usage

```bash
# Single city (token + city ID auto-detected from frontend)
python citybus_gtfs.py --subdomain katerini

# All cities
python generate_all.py
```

Output goes to `output/`.

## Validation

Upload generated zips to https://gtfs-validator.mobilitydata.org/

## Supported Cities

Alexandroupoli, Arta, Chalkida, Chania, Chios, Corfu, Drama, Heraklion, Ioannina, Kastoria, Katerini, Kavala, Komotini, Kozani, Lamia, Larissa, Mytilini, Naousa, Patra, Ptolemaida, Salamina, Serres, Skiathos, Veroia, Volos, Xanthi

Not generating usable data: Agrinio (no schedules), Messolonghi (no schedules)

## Files

- `citybus_gtfs.py` — main generator script
- `generate_all.py` — batch runner for all cities
- `citybus-api.yaml` — OpenAPI 3.0 spec for the CityBus.gr REST API
- `output/` — generated GTFS zips (gitignored)

## Known Limitations

- Stop names are ALL CAPS (as returned by the API)
- Calendar is synthetic (weekday/saturday/sunday) — no holiday handling
- Some operators have routes defined but no trip schedules populated
- No `calendar_dates.txt` or fare data

## Licensing

The CityBus.gr API has no published terms of use. Schedule data licensing is unclear. Do not redistribute generated feeds without confirming with the operators or platform provider (e-analysis, `info@e-analysis.eu`).
