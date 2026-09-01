import pdfplumber
import pandas as pd
import os
import re
import argparse
import glob
import sys

def clean_name(name):
    if not name:
        return ""
    return name.replace("\n", " ").strip()

def clean_number(num_str):
    if not num_str:
        return 0.0
    cleaned = str(num_str).replace(",", "")
    if cleaned.endswith("-"):
        cleaned = "-" + cleaned[:-1]
    try:
        return float(cleaned)
    except ValueError:
        return 0.0

def parse_pdf(file_path):
    all_data = []
    
    print(f"\nOpening {file_path}...")
    try:
        with pdfplumber.open(file_path) as pdf:
            for i, page in enumerate(pdf.pages):
                print(f"Parsing page {i+1}/{len(pdf.pages)}...", end="\r")
                tables = page.extract_tables()
                
                for table in tables:
                    for row in table:
                        if len(row) >= 11:
                            no_col = row[0]
                            if no_col and no_col.strip().isdigit():
                                waktu_raw = clean_name(row[1])
                                tanggal_match = re.search(r'(\d{2}-\d{2}-\d{4})', waktu_raw)
                                tanggal = tanggal_match.group(1) if tanggal_match else waktu_raw
                                
                                data = {
                                    "No": no_col.strip(),
                                    "Tanggal": tanggal,
                                    "Nama Pengirim": clean_name(row[3]),
                                    "Nama Penerima": clean_name(row[5]),
                                    "Debet": clean_number(row[8]),
                                    "Kredit": clean_number(row[9]),
                                    "Saldo Riil": clean_number(row[10])
                                }
                                all_data.append(data)
        print(f"\nParsing complete.")
    except Exception as e:
        print(f"Error reading PDF: {e}")
        return None
    
    return all_data

def select_pdf_interactively():
    pdfs = glob.glob("*.pdf")
    if not pdfs:
        print("No PDF files found in the current directory.")
        return None
    
    print("\nAvailable PDF files:")
    for i, pdf in enumerate(pdfs):
        print(f"[{i+1}] {pdf}")
    
    while True:
        try:
            choice = input(f"\nSelect a file number (1-{len(pdfs)}) or 'q' to quit: ")
            if choice.lower() == 'q':
                return None
            idx = int(choice) - 1
            if 0 <= idx < len(pdfs):
                return pdfs[idx]
        except ValueError:
            pass
        print("Invalid selection. Please try again.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Parse BSI Bank Statement PDF to Excel.")
    parser.add_argument("input", nargs="?", help="Path to the PDF file to parse.")
    args = parser.parse_args()

    pdf_file = args.input

    # If no file provided as argument, enter interactive mode
    if not pdf_file:
        pdf_file = select_pdf_interactively()

    if pdf_file:
        if not os.path.exists(pdf_file):
            print(f"File {pdf_file} not found.")
            sys.exit(1)

        # Generate output name based on input
        base_name = os.path.splitext(os.path.basename(pdf_file))[0]
        output_file = f"Parsed_{base_name}.xlsx"
        
        transactions = parse_pdf(pdf_file)
        if transactions:
            df = pd.DataFrame(transactions)
            cols = ["Tanggal", "Nama Pengirim", "Nama Penerima", "Debet", "Kredit", "Saldo Riil"]
            df = df[[c for c in cols if c in df.columns]]
            
            df["Debet"] = df["Debet"].abs()
            df["Kredit"] = df["Kredit"].abs()
            
            df.to_excel(output_file, index=False)
            print(f"Successfully parsed {len(transactions)} transactions.")
            print(f"Output saved to: {output_file}")
        else:
            print("No transactions found or error occurred.")
    else:
        print("Exiting.")
