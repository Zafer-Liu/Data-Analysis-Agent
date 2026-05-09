import os
import re
import pandas as pd
from typing import List


def export_to_excel(datasource, tables: List[str], filepath: str) -> str:
    """
    Query *tables* from *datasource* and write each as a separate sheet
    in an Excel file at *filepath*.  Returns filepath on success.
    Raises ValueError if no table could be exported.
    """
    os.makedirs(os.path.dirname(filepath), exist_ok=True)

    with pd.ExcelWriter(filepath, engine="openpyxl") as writer:
        written = 0
        for table in tables:
            try:
                df, err = datasource.execute_query(f'SELECT * FROM "{table}"')
                if err or df is None or df.empty:
                    continue
                # Excel sheet name: max 31 chars, no special chars
                sheet_name = re.sub(r'[\\/*?:\[\]]', '_', table)[:31]
                df.to_excel(writer, sheet_name=sheet_name, index=False)
                written += 1
            except Exception:
                continue

    if written == 0:
        raise ValueError("没有可导出的表格数据，请确认表名正确且数据不为空。")

    return filepath
