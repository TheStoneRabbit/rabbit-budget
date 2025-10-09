import argparse
import sys

from storage import NotFoundError
from transaction_processor import process_transactions

# === MAIN ===
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Process credit card transactions and categorize them.")
    parser.add_argument("input_file", help="Path to the input CSV file (e.g., Citi transaction export).")
    parser.add_argument("output_file", help="Path to save the categorized output CSV file.")
    parser.add_argument("--profile", help="The user profile to use.", default="default")

    args = parser.parse_args()

    print(f"Processing input file: {args.input_file}")
    try:
        processed_output_path = process_transactions(args.input_file, args.output_file, args.profile)
    except NotFoundError as err:
        print(f"Error: {err}")
        sys.exit(1)

    print("✅ Categorized transactions:")
    print("📚 Find the output in:", processed_output_path)
