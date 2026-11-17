"""Simple Car Parking Entry/Exit Control for Goltrader Project.

Features:
- Register car entry (plate + timestamp)
- Register car exit (calculate duration and fee)
- View all records
- Data stored in CSV file
"""

import csv
import os
import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime
from pathlib import Path

DATA_FILE = Path(__file__).parent / "parking_data.csv"


def init_csv():
    """Create CSV file with header if it doesn't exist."""
    if not DATA_FILE.exists():
        with open(DATA_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Car Plate", "Entry Time", "Exit Time", "Duration (min)", "Fee (R$)"])


def load_records():
    """Load all records from CSV."""
    records = []
    if DATA_FILE.exists():
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                records.append(row)
    return records


def save_record(plate, entry_time, exit_time, duration_min, fee):
    """Append a record to CSV."""
    with open(DATA_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([plate, entry_time, exit_time, duration_min, fee])


class ParkingControlApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Controle de Estacionamento - Goltrader")
        self.root.geometry("600x400")

        # Initialize CSV
        init_csv()

        # Top frame: Input fields
        top = tk.LabelFrame(root, text="Entrada de Carro", padx=10, pady=10)
        top.pack(fill="x", padx=10, pady=5)

        tk.Label(top, text="Placa do Carro:").grid(row=0, column=0, sticky="w")
        self.plate_entry = tk.Entry(top, width=20)
        self.plate_entry.grid(row=0, column=1, padx=5, pady=2)

        self.entry_btn = tk.Button(top, text="Registrar Entrada", bg="green", fg="white",
                                   command=self.on_entry, width=20)
        self.entry_btn.grid(row=0, column=2, padx=5, pady=2)

        # Middle frame: Records list
        mid = tk.LabelFrame(root, text="Histórico de Movimentação", padx=10, pady=10)
        mid.pack(fill="both", expand=True, padx=10, pady=5)

        columns = ("placa", "entrada", "saida", "duracao", "taxa")
        self.tree = ttk.Treeview(mid, columns=columns, show="headings", height=10)

        for col in columns:
            self.tree.heading(col, text=col.replace("_", " ").title())
            self.tree.column(col, width=100, anchor="center")

        scroll = tk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)

        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

        # Bottom frame: Fee info
        bottom = tk.LabelFrame(root, text="Informações", padx=10, pady=10)
        bottom.pack(fill="x", padx=10, pady=5)

        tk.Label(bottom, text="Taxa padrão por hora: R$ 20,00").pack(anchor="w")
        tk.Label(bottom, text="Fração de hora cobrada como cheia: Sim").pack(anchor="w")

        # Status bar
        self.status = tk.Label(root, text="Pronto", bd=1, relief=tk.SUNKEN, anchor="w")
        self.status.pack(fill="x", padx=10, pady=5)

        self.refresh_list()

    def on_entry(self):
        """Handle entry button click."""
        plate = self.plate_entry.get().strip()
        if not plate:
            messagebox.showwarning("Atenção", "Digite a placa do carro.")
            return
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        # Record entry: store plate + entry time, exit will be blank
        # We need to find if car is already inside
        records = load_records()
        active = [r for r in records if r["saida"] == "" and r["placa"] == plate]
        if active:
            messagebox.showinfo("Informação", "Este carro já está dentro do estacionamento.")
            self.plate_entry.delete(0, tk.END)
            return
        # Add entry record with empty exit
        save_record(plate, now, "", "", "")
        self.refresh_list()
        self.plate_entry.delete(0, tk.END)
        self.status.config(text=f"Carro {plate} entrou às {now}")

    def calculate_fee(self, entry_str, exit_str):
        """Calculate duration and fee."""
        try:
            fmt = "%Y-%m-%d %H:%M:%S"
            entry_dt = datetime.strptime(entry_str, fmt)
            exit_dt = datetime.strptime(exit_str, fmt) if exit_str else None
        except ValueError:
            return 0, 0.0
        if exit_dt is None:
            return 0, 0.0
        minutes = int((exit_dt - entry_dt).total_seconds() / 60)
        # R$20 per hour, fraction counts as full hour
        hours = max(1, (minutes + 59) // 60)  # round up to nearest hour
        fee = hours * 20.0
        return minutes, fee

    def on_exit(self):
        """Handle exit - we need to select a record."""
        selection = self.tree.selection()
        if not selection:
            messagebox.showwarning("Atenção", "Selecione um carro na lista.")
            return
        item = self.tree.item(selection[0])
        values = item["values"]
        # values: (plate, entry, exit, duration, fee)
        if values[3]:  # already has exit
            messagebox.showinfo("Informação", "Este carro já saiu.")
            return
        plate = values[0]
        entry_str = values[1]
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        minutes, fee = self.calculate_fee(entry_str, now)
        # Update the record: we need to rewrite the CSV
        records = load_records()
        for rec in records:
            if rec["placa"] == plate and rec["saida"] == "":
                rec["saida"] = now
                rec["duracao (min)"] = str(minutes)
                rec["taxa (R$)"] = f"{fee:.2f}"
                break
        # Rewrite CSV
        with open(DATA_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["Car Plate", "Entry Time", "Exit Time", "Duration (min)", "Fee (R$)"]
            )
            for rec in records:
                writer.writerow([rec.get("Car Plate", ""), rec.get("Entry Time", ""),
                                 rec.get("Exit Time", ""), rec.get("Duration (min)", ""),
                                 rec.get("Fee (R$)", "")])
        self.refresh_list()
        self.status.config(text=f"Carro {plate} saiu às {now}. "
                                f"Duração: {minutes} min. Taxa: R$ {fee:.2f}")

    def refresh_list(self):
        """Refresh the treeview with current records."""
        # Clear
        for i in self.tree.get_children():
            self.tree.delete(i)
        records = load_records()
        for rec in records:
            # rec has keys from header: Car Plate, Entry Time, Exit Time, Duration (min), Fee (R$)
            plate = rec.get("Car Plate", "")
            entry = rec.get("Entry Time", "")
            exit_t = rec.get("Exit Time", "")
            dur = rec.get("Duration (min)", "")
            fee = rec.get("Fee (R$)", "")
            self.tree.insert("", "end", values=(plate, entry, exit_t, dur, fee))


def main():
    root = tk.Tk()
    app = ParkingControlApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()