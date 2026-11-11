"""Write golden.json with what the my-finance dashboard shows for dataset.json.

Run it with the Python of the my-finance virtualenv, from a checkout of it:

    <my-finance>/.venv/bin/python -B tests/fixtures/parity/generate_golden.py \\
        --my-finance <my-finance>

The real dashboard code (query, decryption and aggregation) runs over an
in-memory SQLite database, so neither PostgreSQL nor the my-finance secrets
are needed. Beyond the standard library, everything comes from the my-finance
checkout and its virtualenv.
"""

import argparse
import base64
import functools
import json
import math
import os
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATASET = HERE / "dataset.json"
GOLDEN = HERE / "golden.json"

# Any valid key works: the golden holds decrypted values only.
FERNET_KEY = base64.urlsafe_b64encode(b"parity-golden-fixed-key-32-bytes")
JWT_SECRET = "parity-golden-jwt-secret"
# list_transactions orders rows by (date, created_at). A fixed created_at keeps
# the order of the float sums, and so every total, the same on each run.
CREATED_AT = datetime(2026, 1, 1)

SUMMARY_FIELDS = (
    "entradas",
    "saidas",
    "investimentos",
    "saldo",
    "avg_daily_expense",
)
ANNUAL_FIELDS = ("entrada", "saida", "investimento", "saldo", "saldo_acumulado")
BY_CATEGORY = ("expenses_by_cat", "income_by_cat")


def dataset_months(transactions: list[dict]) -> list[tuple[int, int]]:
    """Every month from the first to the last readable date, both included."""
    dates = []
    for transaction in transactions:
        try:
            dates.append(datetime.strptime(transaction["date"], "%Y-%m-%d"))
        except ValueError:
            continue
    first, last = min(dates), max(dates)
    months = []
    year, month = first.year, first.month
    while (year, month) <= (last.year, last.month):
        months.append((year, month))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


def dashboard(checkout: Path, dataset: dict) -> dict:
    """get_dashboard_data of each dataset month, over an in-memory database."""
    # Set before my-finance is imported; load_dotenv keeps variables that
    # already exist, so the checkout's .env never supplies these.
    os.environ["FERNET_KEY"] = FERNET_KEY.decode("ascii")
    os.environ["JWT_SECRET"] = JWT_SECRET
    sys.path.insert(0, str(checkout))

    from models import Base, Category, Transaction, User
    from repositories import base_repository, transactions_repository
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from utils.crypto import encrypt
    from utils.data_format_utils import days_in_period

    # One shared connection: each new connection to "sqlite://" would open
    # a different, empty database.
    engine = create_engine("sqlite://", poolclass=StaticPool)
    tables = [User.__table__, Category.__table__, Transaction.__table__]
    Base.metadata.create_all(engine, tables=tables)
    base_repository._engine = engine
    base_repository._session = sessionmaker(bind=engine, expire_on_commit=False)
    # The dashboard counts elapsed days of the current month from date.today().
    reference = date.fromisoformat(dataset["reference_date"])
    transactions_repository.days_in_period = functools.partial(
        days_in_period, today=reference
    )

    user_id = dataset["user_id"]
    stamps = {"created_at": CREATED_AT, "updated_at": CREATED_AT}
    with base_repository.get_session() as session:
        session.add(
            User(id=user_id, username="parity", password_hash="-", **stamps)
        )
        for category in dataset["categories"]:
            session.add(
                Category(
                    id=category["id"],
                    user_id=user_id,
                    name=encrypt(category["name"]),
                    type=encrypt(category["type"]),
                    **stamps,
                )
            )
        for row in dataset["transactions"]:
            # Rows saved before my-finance encrypted its columns hold plain
            # text, which its decrypt returns as is.
            seal = str if row["plaintext"] else encrypt
            session.add(
                Transaction(
                    id=row["id"],
                    user_id=user_id,
                    category_id=row["category_id"],
                    date=seal(row["date"]),
                    year=row["year"],
                    description=(
                        seal(row["description"]) if row["description"] else None
                    ),
                    value=seal(row["value"]),
                    installment_group=row["installment_group"],
                    installment_number=row["installment_number"],
                    installment_total=row["installment_total"],
                    **stamps,
                )
            )
        session.commit()

    months = {}
    annual = {}
    repository = transactions_repository.TransactionsRepository
    for year, month in dataset_months(dataset["transactions"]):
        data = repository.get_dashboard_data(user_id, year, month)
        months[f"{year}-{month:02d}"] = {
            "summary": data["summary"],
            "expenses_by_cat": data["expenses_by_cat"],
            "income_by_cat": data["income_by_cat"],
        }
        # The annual series depends on the year only.
        annual[str(year)] = data["annual"]
    return {"months": months, "annual": annual}


def checkout_commit(checkout: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"],
        capture_output=True,
        check=True,
        text=True,
    ).stdout.strip()


def near_half_cent(value: float) -> bool:
    half = (math.floor(abs(value) * 100) + 0.5) / 100
    return abs(abs(value) - half) < 1e-6


def half_cent_values(golden: dict) -> list[str]:
    """Compared values that rounding to cents could split between versions.

    The dashboard sums in float and the server in Decimal. A value this close
    to half a cent could round to different cents for a reason unrelated to
    the business rule, so the dataset has to avoid it.
    """
    found = []
    for key, month in golden["months"].items():
        for field in SUMMARY_FIELDS:
            value = month["summary"][field]
            if near_half_cent(value):
                found.append(f"{key} summary.{field}: {value!r}")
        for listing in BY_CATEGORY:
            for item in month[listing]:
                if near_half_cent(item["total"]):
                    found.append(
                        f"{key} {listing}[{item['category']}]: "
                        f"{item['total']!r}"
                    )
    for year, points in golden["annual"].items():
        for point in points:
            for field in ANNUAL_FIELDS:
                if near_half_cent(point[field]):
                    found.append(
                        f"{year}-{point['month']} annual.{field}: "
                        f"{point[field]!r}"
                    )
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--my-finance", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=GOLDEN)
    args = parser.parse_args(argv)
    checkout = args.my_finance.resolve()

    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    golden = {
        "reference_date": dataset["reference_date"],
        "my_finance_commit": checkout_commit(checkout),
        **dashboard(checkout, dataset),
    }
    problems = half_cent_values(golden)
    if problems:
        print("Values within 1e-6 of half a cent:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        print(
            "Adjust dataset.json; golden.json was not written.", file=sys.stderr
        )
        return 1

    text = json.dumps(golden, ensure_ascii=False, indent=2, sort_keys=True)
    args.out.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
