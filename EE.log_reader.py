import re
import os
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk
from tkinterdnd2 import DND_FILES, TkinterDnD
from datetime import datetime
from pathlib import Path
import subprocess

def parse_log(file_path, min_keyword_filter=True, use_utc=False):
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        contents = f.read()

    lines = contents.splitlines()
    player_name = None
    start_time = None
    end_time = None

    # Player name detection
    login_rx = re.compile(r'^([0-9\.]+) Sys \[Info\]: Logged in (\S+)')
    for L in lines:
        if m := login_rx.match(L):
            player_name = m.group(2)
            break

    # Start time detection
    diag_rx = re.compile(r'^([0-9\.]+) Sys \[Diag\]: Current time: [^\[]+\[UTC: ([^\]]+)\]')
    for L in lines:
        if m := diag_rx.match(L):
            offset = float(m.group(1))
            utc_dt = datetime.strptime(m.group(2), "%a %b %d %H:%M:%S %Y")
            start_time = utc_dt.timestamp() - offset
            break
    if start_time is None:
        start_time = Path(file_path).stat().st_mtime

    # End time detection
    ts_rx = re.compile(r'^([0-9\.]+)')
    for L in reversed(lines):
        if m := ts_rx.match(L):
            end_time = start_time + float(m.group(1))
            break
    else:
        end_time = start_time

    # Event parsing
    event_rx = re.compile(r"([0-9\.]+) Game \[Info\]: ([^\r\n]+?) was ([^ ]+) by ([^\r\n]+?) damage ?([^\r\n]*)")
    warn_rx = re.compile(r'^([0-9\.]+) Game \[Warning\]:\s*(.*)')

    combat_events = []
    warning_groups = {}
    event_offsets = set()

    # Process combat events
    for m in event_rx.finditer(contents):
        off = float(m.group(1))
        event_offsets.add(off)
        t_format = datetime.utcfromtimestamp if use_utc else datetime.fromtimestamp
        t = t_format(start_time + off).strftime("%H:%M:%S")
        
        victim = m.group(2)
        state = m.group(3)
        damage_info = m.group(4)
        source = m.group(5).strip() if m.group(5) else 'from an unknown source'

        if victim == "RAZORFLIES":
            continue

        # Health/damage split
        health, damage = "unknown", damage_info
        if " / " in damage_info:
            parts = damage_info.split(" / ")
            if len(parts) == 2:
                health, damage = parts

        # Format message
        if state == "downed":
            message = f"{t} - <{victim}> downed at {health} health {source.replace('from a', 'by a')}"
        else:
            message = f"{t} - <{victim}> {state} by {damage} damage at {health} health {source}"

        combat_events.append({
            'Type': 'Combat',
            'Offset': off,
            'Time': t,
            'Victim': victim,
            'State': state,
            'Damage': damage,
            'Health': health,
            'Source': source,
            'Message': message,
            'Value': float(damage) if damage.replace('.', '').isdigit() else 0
        })

    # Process warnings
    for L in lines:
        if m := warn_rx.match(L):
            text = m.group(2)
            if min_keyword_filter and not re.search(r'dmg|damage', text, re.IGNORECASE):
                continue
            if text.strip().startswith("Cannot create"): # Skip all the cannot create warnings since sometiimes they are with damage in them and are quite spammy
                continue
            off = float(m.group(1))
            warning_groups.setdefault(off, []).append(text.strip())

    # Process warning groups
    warning_list = []
    for off, wlist in warning_groups.items():
        if off in event_offsets:
            continue
            
        group = {
            'total': len(wlist),
            'max_dmg': 0.0,
            'messages': [],
            'dmg_vals': []
        }
        
        for text in wlist:
            if m_val := re.search(r'high dmg:\s*([0-9\.eE+\-]+)', text):
                dmg_val = float(m_val.group(1))
                group['max_dmg'] = max(group['max_dmg'], dmg_val)
                group['dmg_vals'].append(dmg_val)
            group['messages'].append(text)

        t_format = datetime.utcfromtimestamp if use_utc else datetime.fromtimestamp
        t = t_format(start_time + off).strftime("%H:%M:%S")
        
        warning_list.append({
            'Type': 'WarningGroup',
            'Offset': off,
            'Time': t,
            'Count': group['total'],
            'MaxDamage': group['max_dmg'],
            'Messages': group['messages'],
            'Children': [{
                'Offset': off,
                'Time': t,
                'Message': msg,
                'Damage': f"{group['dmg_vals'][idx]:.2e}" if idx < len(group['dmg_vals']) else '',
                'Value': group['dmg_vals'][idx] if idx < len(group['dmg_vals']) else 0
            } for idx, msg in enumerate(group['messages'])]
        })

    return {
        'Player': player_name or '',
        'LogStart': datetime.fromtimestamp(start_time).strftime('%Y-%m-%d %H:%M:%S'),
        'LogEnd': datetime.fromtimestamp(end_time).strftime('%Y-%m-%d %H:%M:%S'),
        'CombatEvents': combat_events,
        'WarningGroups': warning_list
    }

class LogReaderGUI(TkinterDnD.Tk):
    def __init__(self):
        super().__init__()
        self.title('Warframe EE.log Analyzer')
        self.geometry('1400x800')
        self.current_path = None
        self.log_data = {}
        self.last_mtime = 0
        self.expanded_groups = set()
        self.sort_info = {'combat': None, 'warnings': None}
        self.filter_info = {'combat': '', 'warnings': ''}
        self.original_rows = {'combat': [], 'warnings': []}
        self.current_rows = {'combat': [], 'warnings': []}

        self.create_widgets()
        self.setup_bindings()
        self.after(100, self.load_default_log)

    def create_widgets(self):
        # Summary label
        self.summary_var = tk.StringVar()
        ttk.Label(self, textvariable=self.summary_var, padding=5).pack(fill='x')

        # Notebook setup
        self.notebook = ttk.Notebook(self)
        
        # Combat Log Tab with auto-sizing columns
        self.combat_frame = ttk.Frame(self.notebook)
        self.combat_tree = ttk.Treeview(self.combat_frame, 
            columns=('Target', 'Health', 'Source', 'Damage', 'Time'), 
            show='headings'
        )
        
        for col in ('Target', 'Health', 'Source', 'Damage', 'Time'):
            self.combat_tree.heading(col, text=col)
            self.combat_tree.column(col, width=50, anchor='w', stretch=True)
        self.combat_tree.pack(fill='both', expand=True, padx=5, pady=5)

        # Damage Analysis Tab
        self.analysis_frame = ttk.Frame(self.notebook)
        self.analysis_tree = ttk.Treeview(self.analysis_frame,
            columns=('Time', 'MaxDamage', 'Count', 'Messages'),
            show='headings'
        )
        
        # Configure columns with appropriate alignment
        analysis_columns = {
            'Time': {'anchor': 'w', 'width': 120},
            'MaxDamage': {'anchor': 'e', 'width': 120},
            'Count': {'anchor': 'center', 'width': 80},
            'Messages': {'anchor': 'w', 'width': 400}
        }
        
        for col, config in analysis_columns.items():
            self.analysis_tree.heading(col, text=col, anchor=config['anchor'])
            self.analysis_tree.column(col, 
                width=config['width'],
                anchor=config['anchor'],
                stretch=True
            )
        
        self.analysis_tree.pack(fill='both', expand=True, padx=5, pady=5)
        
        self.notebook.add(self.combat_frame, text='Death Log')
        self.notebook.add(self.analysis_frame, text='Damage Analysis')
        self.notebook.pack(fill='both', expand=True)

        # Control Panel
        control_frame = ttk.Frame(self)
        control_frame.pack(fill='x', pady=5)
        
        self.filter_var = tk.StringVar()
        self.filter_entry = ttk.Entry(control_frame, textvariable=self.filter_var)
        self.filter_entry.pack(side='left', fill='x', expand=True, padx=5)
        ttk.Button(control_frame, text='Clear Filter', command=self.clear_filter).pack(side='left', padx=2)
        
        ttk.Button(control_frame, text='Open', command=self.open_file).pack(side='left', padx=5)
        ttk.Button(control_frame, text='Refresh', command=self.refresh).pack(side='left', padx=5)
        self.auto_refresh_var = tk.BooleanVar()
        ttk.Checkbutton(control_frame, text='Auto-Refresh', variable=self.auto_refresh_var).pack(side='left', padx=5)
        self.utc_var = tk.BooleanVar()
        ttk.Checkbutton(control_frame, text='UTC', variable=self.utc_var, command=self.toggle_utc).pack(side='left', padx=5)
        
        # Context menu
        self.context_menu = tk.Menu(self, tearoff=0)
        self.context_menu.add_command(label="Copy Row", command=self.copy_row)
        self.context_menu.add_command(label="Export to CSV...", command=self.export_csv)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="Open Log File", command=self.open_in_editor)

    def setup_bindings(self):
        self.drop_target_register(DND_FILES)
        self.dnd_bind('<<Drop>>', self.handle_drop)
        self.combat_tree.bind('<Button-3>', self.show_context_menu)
        self.analysis_tree.bind('<Button-3>', self.show_context_menu)
        self.analysis_tree.bind('<Double-1>', self.toggle_warning_group)
        self.filter_var.trace_add('write', self.apply_filter)
        self.auto_refresh_var.trace_add('write', lambda *_: self.toggle_auto_refresh())

    def load_default_log(self):
        localappdata = os.getenv('LOCALAPPDATA', '')
        if not localappdata:
            messagebox.showerror("Error", "%LOCALAPPDATA% environment variable not found!")
            return
            
        default_path = Path(localappdata) / 'Warframe' / 'EE.log'
        if default_path.exists():
            try:
                self.load_log(default_path)
            except Exception as e:
                messagebox.showerror("Load Error", 
                    f"Failed to load default log:\n{e}\n"
                    "Try opening the file manually."
                )
        else:
            messagebox.showinfo("Info", 
                "Default EE.log not found in:\n"
                f"{default_path}\n"
                "Drag & drop a log file or use File > Open."
            )

    def update_display(self):
        self.summary_var.set(
            f"Player: {self.log_data.get('Player', 'N/A')} | "
            f"Start: {self.log_data.get('LogStart', 'N/A')} | "
            f"End: {self.log_data.get('LogEnd', 'N/A')}"
        )

        # combat log with detailed columns
        self.combat_tree.delete(*self.combat_tree.get_children())
        for event in self.current_rows['combat']:
            self.combat_tree.insert('', 'end', values=(
                event['Victim'],          # Target
                event['Health'],          # Health
                event['Source'],          # Source (who + what)
                event['Damage'],          # Damage
                event['Time']             # Time
            ))

        self.analysis_tree.delete(*self.analysis_tree.get_children())
        for group in self.current_rows['warnings']:
            parent = self.analysis_tree.insert('', 'end', values=(
                group['Time'],
                f"{group['MaxDamage']:.2e}",
                group['Count'],
                '; '.join(group['Messages'][:3]) + ('...' if len(group['Messages']) > 3 else '')
            ))
            if group['Offset'] in self.expanded_groups:
                for child in group['Children']:
                    self.analysis_tree.insert(parent, 'end', values=(
                        child['Time'],
                        child['Damage'],
                        '',
                        child['Message']
                    ))

        self.auto_resize_columns()

    def auto_resize_columns(self):
        def resize_tree(tree):
            font = tkfont.Font()
            min_widths = {
                'Time': 120,
                'MaxDamage': 140,
                'Count': 80,
                'Messages': 200  # Base width for messages
            }
            
            for col in tree['columns']:
                # Calculate header width
                header_text = tree.heading(col)['text']
                max_w = font.measure(header_text) + 30
                
                # Check all items (including children of expanded groups)
                def check_children(parent):
                    for item in tree.get_children(parent):
                        # Check the item itself
                        cell_value = tree.set(item, col)
                        cell_width = font.measure(cell_value) + 30
                        if col == 'MaxDamage' and tree.heading(col)['anchor'] == 'e':
                            cell_width += 20  # Extra space for right-aligned numbers
                        nonlocal max_w
                        max_w = max(max_w, cell_width)
                        
                        # Recursively check children if expanded
                        if tree.exists(item) and tree.item(item, 'open'):
                            check_children(item)
                
                # Start with root items
                check_children('')
                
                # Apply minimum width constraints
                min_w = min_widths.get(col, 100)
                max_w = max(max_w, min_w)
                
                # Set column width with constraints
                tree.column(col, width=min(max_w, 600))  # Max width cap

        resize_tree(self.combat_tree)
        resize_tree(self.analysis_tree)
        
    def apply_filter(self, *args):
        current_tab = 'combat' if self.notebook.index("current") == 0 else 'warnings'
        self.filter_info[current_tab] = self.filter_var.get().lower()
        
        filtered = []
        for row in self.original_rows[current_tab]:
            if current_tab == 'combat':
                # Search across all relevant combat fields
                match_fields = ['Victim', 'Health', 'Source', 'Damage']
                search_text = [
                    str(row['Victim']).lower(),
                    str(row['Health']).lower(),
                    str(row['Source']).lower(),
                    str(row['Damage']).lower()
                ]
            else:
                match_fields = ['Messages']
            
            if any(self.filter_info[current_tab] in str(row.get(field, '')).lower() 
                   for field in match_fields):
                filtered.append(row)
        
        self.current_rows[current_tab] = filtered
        self.sort_and_display(current_tab)

    def clear_filter(self):
        self.filter_var.set('')
        self.current_rows = {k: v.copy() for k, v in self.original_rows.items()}
        self.update_display()

    def sort_and_display(self, tab):
        if self.sort_info[tab]:
            col, reverse = self.sort_info[tab]
            key_map = {
                'combat': {'Value': 'Value', 'Damage': 'Value'},
                'warnings': {'MaxDamage': 'MaxDamage', 'Count': 'Count'}
            }
            key = key_map[tab].get(col, col)
            self.current_rows[tab].sort(key=lambda x: x.get(key, 0), reverse=reverse)
        self.update_display()

    def toggle_warning_group(self, event):
        item = self.analysis_tree.identify_row(event.y)
        if item and self.analysis_tree.exists(item):
            values = self.analysis_tree.item(item, 'values')
            if values[2]:
                offset = next((g['Offset'] for g in self.current_rows['warnings'] 
                             if g['Time'] == values[0] and g['Count'] == int(values[2])), None)
                if offset:
                    if offset in self.expanded_groups:
                        self.expanded_groups.remove(offset)
                        self.analysis_tree.delete(*self.analysis_tree.get_children(item))
                    else:
                        self.expanded_groups.add(offset)
                        group = next(g for g in self.current_rows['warnings'] if g['Offset'] == offset)
                        for child in group['Children']:
                            self.analysis_tree.insert(item, 'end', values=(
                                child['Time'],
                                child['Damage'],
                                '',
                                child['Message']
                            ))

    def show_context_menu(self, event):
        try:
            x = self.winfo_pointerx()
            y = self.winfo_pointery()
            self.context_menu.tk_popup(x, y, 0)
        finally:
            self.context_menu.grab_release()

    def copy_row(self):
        current_tab = 'combat' if self.notebook.index("current") == 0 else 'warnings'
        tree = self.combat_tree if current_tab == 'combat' else self.analysis_tree
        selected = tree.selection()
        if selected:
            item = selected[0]
            values = tree.item(item, 'values')
            self.clipboard_clear()
            self.clipboard_append('\t'.join(values))

    def export_csv(self):
        current_tab = 'combat' if self.notebook.index("current") == 0 else 'warnings'
        tree = self.combat_tree if current_tab == 'combat' else self.analysis_tree
        path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV Files", "*.csv")])
        if path:
            with open(path, 'w', encoding='utf-8') as f:
                headers = tree['columns']
                f.write(','.join(headers) + '\n')
                for item in tree.get_children():
                    f.write(','.join(tree.item(item, 'values')) + '\n')

    def toggle_auto_refresh(self):
        if self.auto_refresh_var.get():
            self.schedule_auto_refresh()
        else:
            self.cancel_auto_refresh()

    def cancel_auto_refresh(self):
        if hasattr(self, 'auto_refresh_id'):
            self.after_cancel(self.auto_refresh_id)
            del self.auto_refresh_id

    def schedule_auto_refresh(self):
        try:
            if self.auto_refresh_var.get() and self.current_path:
                current_mtime = os.path.getmtime(self.current_path)
                if current_mtime > self.last_mtime:
                    self.last_mtime = current_mtime
                    self.load_log(self.current_path)
                self.auto_refresh_id = self.after(2000, self.schedule_auto_refresh)
        except Exception as e:
            messagebox.showerror('Refresh Error', f'Auto-refresh failed: {e}')
            self.cancel_auto_refresh()

    def toggle_utc(self):
        if self.current_path:
            self.load_log(self.current_path)

    def open_in_editor(self):
        if self.current_path:
            try:
                if os.name == 'nt':
                    os.startfile(self.current_path)
                else:
                    subprocess.call(['xdg-open', self.current_path])
            except Exception as e:
                messagebox.showerror('Error', f'Failed to open editor: {e}')
        else:
            messagebox.showinfo('Info', 'No log file loaded.')

    def open_file(self):
        path = filedialog.askopenfilename(filetypes=[('Log files','*.log'), ('All','*')])
        if path:
            self.load_log(path)

    def refresh(self):
        if self.current_path:
            self.load_log(self.current_path)
        else:
            messagebox.showinfo('Refresh', 'No file loaded to refresh.')

    def handle_drop(self, e):
        path = e.data.strip('{}')
        self.load_log(path)

    def load_log(self, path):
        try:
            self.cancel_auto_refresh()
            if not os.access(path, os.R_OK):
                raise PermissionError("File is locked or inaccessible")
            
            use_utc = self.utc_var.get()
            parsed_data = parse_log(path, use_utc=use_utc)
            
            self.current_path = path
            self.log_data = parsed_data
            self.original_rows['combat'] = parsed_data['CombatEvents']
            self.original_rows['warnings'] = parsed_data['WarningGroups']
            self.current_rows['combat'] = parsed_data['CombatEvents'].copy()
            self.current_rows['warnings'] = parsed_data['WarningGroups'].copy()
            self.last_mtime = os.path.getmtime(path)
            
            if self.auto_refresh_var.get():
                self.schedule_auto_refresh()
                
            self.filter_var.set('')
            self.sort_info = {'combat': None, 'warnings': None}
            self.expanded_groups.clear()
            self.update_display()
        except PermissionError as e:
            messagebox.showerror('Error', 
                f"Could not read {Path(path).name}:\n"
                "Make sure Warframe is closed or the file isn't in use."
            )
        except Exception as e:
            messagebox.showerror('Error', f'Failed to parse log:\n{e}')

if __name__ == '__main__':
    try:
        LogReaderGUI().mainloop()
    except Exception as e:
        messagebox.showerror('Critical Error', f'The application has crashed:\n{str(e)}')