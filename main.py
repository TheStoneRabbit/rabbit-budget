import argparse
from transaction_processor import process_transactions

# === MAIN ===
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Process credit card transactions and categorize them.")
    parser.add_argument("input_file", help="Path to the input CSV file (e.g., Citi transaction export).")
    parser.add_argument("output_file", help="Path to save the categorized output CSV file.")
    parser.add_argument("--profile", help="The user profile to use.", default="default")

    args = parser.parse_args()

    print(f"Processing input file: {args.input_file}")
    processed_output_path = process_transactions(args.input_file, args.output_file, args.profile)

    print("✅ Categorized transactions:")
    print("📚 Find the output in:", processed_output_path)