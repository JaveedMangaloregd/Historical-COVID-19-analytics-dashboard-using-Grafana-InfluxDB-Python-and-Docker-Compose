
from pathlib import Path

import pandas as pd
from influxdb import InfluxDBClient


# --------------------------------------------------
# Project configuration
# --------------------------------------------------

PROJECT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_DIR / "data"

BATCH_SIZE = 5000

client = InfluxDBClient(
    host="localhost",
    port=8086,
    database="covid19",
    timeout=30,
)


def clean_number(value):
    """Return a finite number, or None when the value is missing."""
    if pd.isna(value):
        return None

    number = float(value)

    if not pd.notna(number):
        return None

    return number


def write_records(measurement, df, date_column, tag_columns, field_columns):
    """Convert a DataFrame into InfluxDB points and write in batches."""
    points = []

    for _, row in df.iterrows():
        timestamp = pd.to_datetime(row[date_column], errors="coerce")

        if pd.isna(timestamp):
            continue

        tags = {}
        for column in tag_columns:
            value = row[column]

            if pd.notna(value) and str(value).strip():
                tags[column] = str(value).strip()
            else:
                tags = None
                break

        if tags is None:
            continue

        fields = {}
        for column in field_columns:
            value = clean_number(row[column])

            if value is not None:
                fields[column] = value

        if not fields:
            continue

        points.append(
            {
                "measurement": measurement,
                "tags": tags,
                "time": timestamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "fields": fields,
            }
        )

        if len(points) >= BATCH_SIZE:
            client.write_points(points, time_precision="s")
            points.clear()

    if points:
        client.write_points(points, time_precision="s")


def ingest_global():
    path = DATA_DIR / "owid-covid-data.csv"
    print(f"\nLoading global data: {path}")

    df = pd.read_csv(
        path,
        usecols=[
            "iso_code",
            "continent",
            "location",
            "date",
            "total_cases",
            "new_cases",
            "total_deaths",
            "new_deaths",
            "population",
        ],
    )

    # Exclude regional aggregates and non-country OWID entries.
    df = df[
        df["iso_code"].notna()
        & ~df["iso_code"].astype(str).str.startswith("OWID_")
    ].copy()

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date", "location", "iso_code"])

    # Use stable, dashboard-friendly field names.
    df = df.rename(
        columns={
            "total_cases": "confirmed",
            "new_cases": "daily_confirmed",
            "total_deaths": "deaths",
            "new_deaths": "daily_deaths",
        }
    )

    fields = [
        "confirmed",
        "daily_confirmed",
        "deaths",
        "daily_deaths",
        "population",
    ]

    write_records(
        measurement="global_covid",
        df=df,
        date_column="date",
        tag_columns=["iso_code", "location", "continent"],
        field_columns=fields,
    )

    print(f"Global rows prepared: {len(df):,}")


def ingest_india_national():
    path = DATA_DIR / "case_time_series.csv"
    print(f"\nLoading India national data: {path}")

    df = pd.read_csv(path)

    df["Date_YMD"] = pd.to_datetime(df["Date_YMD"], errors="coerce")
    df = df.dropna(subset=["Date_YMD"])

    df = df.rename(
        columns={
            "Total Confirmed": "confirmed",
            "Daily Confirmed": "daily_confirmed",
            "Total Recovered": "recovered",
            "Daily Recovered": "daily_recovered",
            "Total Deceased": "deaths",
            "Daily Deceased": "daily_deaths",
        }
    )

    write_records(
        measurement="india_national",
        df=df,
        date_column="Date_YMD",
        tag_columns=[],
        field_columns=[
            "confirmed",
            "daily_confirmed",
            "recovered",
            "daily_recovered",
            "deaths",
            "daily_deaths",
        ],
    )

    print(f"India national rows prepared: {len(df):,}")


def ingest_india_states():
    path = DATA_DIR / "states.csv"
    print(f"\nLoading India state data: {path}")

    df = pd.read_csv(path)

    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date", "State"])

    # Exclude national totals from the state-level measurement.
    df = df[
        df["State"].astype(str).str.strip().str.lower() != "india"
    ].copy()

    df = df.rename(
        columns={
            "Confirmed": "confirmed",
            "Recovered": "recovered",
            "Deceased": "deaths",
            "Tested": "tested",
            "Other": "other",
        }
    )

    write_records(
        measurement="india_state",
        df=df,
        date_column="Date",
        tag_columns=["State"],
        field_columns=[
            "confirmed",
            "recovered",
            "deaths",
            "tested",
            "other",
        ],
    )

    print(f"India state rows prepared: {len(df):,}")


def main():
    try:
        if not client.ping():
            raise RuntimeError("InfluxDB did not respond to ping.")

        databases = client.get_list_database()
        if not any(db["name"] == "covid19" for db in databases):
            client.create_database("covid19")

        client.switch_database("covid19")

        for filename in [
            "owid-covid-data.csv",
            "case_time_series.csv",
            "states.csv",
        ]:
            path = DATA_DIR / filename
            if not path.exists():
                raise FileNotFoundError(f"Missing CSV file: {path}")

        ingest_global()
        ingest_india_national()
        ingest_india_states()

        print("\nIngestion finished.")
        print("Measurements:", client.get_list_measurements())

    finally:
        client.close()


if __name__ == "__main__":
    main()
