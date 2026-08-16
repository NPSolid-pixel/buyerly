#!/usr/bin/env python3
import sqlite3
import json
import os
import sys

def migrate(db_path):
    print(f"Migrating database at: {db_path}")
    if not os.path.exists(db_path):
        print("Database not found!")
        return

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Get existing columns in accounts table
    cursor.execute("PRAGMA table_info(accounts);")
    columns = [row['name'] for row in cursor.fetchall()]

    if 'active_rules' not in columns:
        print("Adding 'active_rules' column to 'accounts' table...")
        cursor.execute("ALTER TABLE accounts ADD COLUMN active_rules TEXT DEFAULT '[]' NOT NULL;")
    else:
        print("'active_rules' column already exists.")

    # Fetch accounts to migrate existing single-rule setups
    cursor.execute("SELECT * FROM accounts")
    accounts = cursor.fetchall()

    update_count = 0
    for acc in accounts:
        # Check if it has a preset_id (using the new local schema)
        has_preset = 'preset_id' in columns and acc['preset_id'] is not None

        active_rules = []

        if has_preset:
            # We migrate the existing rule to the new active_rules array
            rule = {
                "preset_id": acc['preset_id'],
                "name": acc.get('preset_name', f"Preset #{acc['preset_id']}"),
                "action": acc.get('rule_action', 'turn_off'),
                "conditions": json.loads(acc.get('rule_conditions', '[]')),
                "logic": acc.get('rule_condition_logic', 'and'),
                "cooldown_minutes": acc.get('rule_cooldown_minutes', 0),
                "check_interval": acc.get('rule_check_interval', 5),
                "notify_tg": acc.get('rule_notify_tg', 1),
                "budget_change_percent": acc.get('rule_budget_change_percent', 0.0),
                "budget_max_daily": acc.get('rule_budget_max_daily', 0.0)
            }
            active_rules.append(rule)
            
            cursor.execute(
                "UPDATE accounts SET active_rules = ? WHERE id = ?",
                (json.dumps(active_rules), acc['id'])
            )
            update_count += 1

    conn.commit()
    print(f"Migrated {update_count} accounts with existing rules.")
    conn.close()
    print("Migration complete!")

if __name__ == "__main__":
    db_file = sys.argv[1] if len(sys.argv) > 1 else "mediabuyer.db"
    migrate(db_file)
