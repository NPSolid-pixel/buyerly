import unittest

from sqlalchemy import DateTime

import database.models  # Register every mapped table in Base.metadata.
from database.db import Base


class TestPostgresDatetimeContract(unittest.TestCase):
    def test_python_defaults_match_postgres_column_timezone_mode(self):
        mismatches = []
        checked = 0
        for table in Base.metadata.sorted_tables:
            for column in table.columns:
                if not isinstance(column.type, DateTime):
                    continue
                if column.default is None or not callable(column.default.arg):
                    continue
                try:
                    value = column.default.arg(None)
                except TypeError:
                    value = column.default.arg()
                checked += 1
                is_aware = value.tzinfo is not None and value.utcoffset() is not None
                if is_aware != bool(column.type.timezone):
                    mismatches.append(
                        f"{table.name}.{column.name}: timezone={column.type.timezone}, value={value!r}"
                    )

        self.assertGreater(checked, 0)
        self.assertEqual(mismatches, [])


if __name__ == "__main__":
    unittest.main()
