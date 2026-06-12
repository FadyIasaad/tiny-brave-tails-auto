from tbt_common import get_all_values, get_sheets_client, get_worksheet, open_spreadsheet

client = get_sheets_client()
spreadsheet = open_spreadsheet(client)
sheet = get_worksheet(spreadsheet, "Content")
values = get_all_values(sheet)
print(f"Connected successfully. Rows found: {len(values)}")
print("Headers:", values[0] if values else [])
