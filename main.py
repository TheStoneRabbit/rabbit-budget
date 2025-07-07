from openai import OpenAI
import os
import pandas as pd
import re
import time
import json
import argparse
from transaction_processor import process_transactions

# === CONFIG ===
categories = [
    ("Eating Out", 500.00),
    ("Groceries", 500.00),
    ("Rent", 1900.00),
    ("Public Transportation", 100.00),
    ("Repairs", 0.00),
    ("Gas", 50.00),
    ("Doctor's Office", 0.00),
    ("Prescriptions", 200.00),
    ("Fun", 600.00),
    ("Going Out", 500.00),
    ("Gifts", 100.00),
    ("Uncategorized", 0.00),
    ("Discretionary", 0.00),
    ("Subscription", 100.00),
]
with open("category_rules.json", "r") as f:
    CATEGORY_RULES = json.load(f)

uncategorized = []

# === MAIN ===
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Process credit card transactions and categorize them.")
    parser.add_argument("input_file", help="Path to the input CSV file (e.g., Citi transaction export).")
    parser.add_argument("output_file", help="Path to save the categorized output CSV file.")

    args = parser.parse_args()

    print(f"Processing input file: {args.input_file}")
    processed_output_path = process_transactions(args.input_file, args.output_file)

    print("✅ Categorized transactions:")
    print("📚 Find the output in:", processed_output_path)