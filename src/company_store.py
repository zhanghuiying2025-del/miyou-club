"""Transactional local simulation records shared by business and operations modules."""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3

COMPANY_DB = Path(__file__).resolve().parents[1] / "data" / "company_simulation.sqlite3"


class SimulationConflict(ValueError):
    pass


class Records:
    def __init__(self, connection):
        self.connection = connection

    def get(self, kind, key):
        record = self.find(kind, key)
        if record is None:
            raise ValueError("记录不存在，请刷新页面")
        return record

    def find(self, kind, key):
        row = self.connection.execute("SELECT payload,revision FROM records WHERE kind=? AND id=?", (kind, key)).fetchone()
        return (json.loads(row[0]), row[1]) if row else None

    def all(self, kind):
        return [json.loads(row[0]) for row in self.connection.execute(
            "SELECT payload FROM records WHERE kind=? ORDER BY rowid DESC", (kind,))]

    def save(self, kind, key, value, revision=None):
        payload = json.dumps(value, ensure_ascii=False, allow_nan=False)
        if revision is None:
            self.connection.execute("INSERT INTO records(kind,id,payload,revision) VALUES(?,?,?,1)", (kind, key, payload))
        else:
            result = self.connection.execute("UPDATE records SET payload=?,revision=revision+1 WHERE kind=? AND id=? AND revision=?",
                (payload, kind, key, revision))
            if result.rowcount != 1:
                raise SimulationConflict("数据已在其他页面变化，请刷新后操作")

    def event(self, key, category, entity_id, payload):
        # A duplicate operation cannot create another cash or coupon event.
        self.connection.execute("INSERT INTO events(id,category,entity_id,payload) VALUES(?,?,?,?)",
            (key, category, entity_id, json.dumps(payload, ensure_ascii=False, allow_nan=False)))

    def events(self, category=None):
        sql = "SELECT id,category,entity_id,payload FROM events"
        rows = self.connection.execute(sql + (" WHERE category=?" if category else "") + " ORDER BY rowid", (category,) if category else ())
        return [{"id":r[0],"category":r[1],"entity_id":r[2],**json.loads(r[3])} for r in rows]


@contextmanager
def transaction():
    COMPANY_DB.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(COMPANY_DB), timeout=15)
    try:
        connection.execute("CREATE TABLE IF NOT EXISTS records(kind TEXT,id TEXT,payload TEXT NOT NULL,revision INTEGER NOT NULL,PRIMARY KEY(kind,id))")
        connection.execute("CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,category TEXT NOT NULL,entity_id TEXT NOT NULL,payload TEXT NOT NULL)")
        connection.commit()
        connection.execute("BEGIN IMMEDIATE")
        yield Records(connection)
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def list_records(kind):
    with transaction() as tx:
        return tx.all(kind)


def read_record(kind, key):
    with transaction() as tx:
        return tx.get(kind, key)


def summarize_cash_events(events):
    incoming = sum(e["cents"] for e in events if e["direction"] == "in")
    outgoing = sum(e["cents"] for e in events if e["direction"] == "out")
    return {"opening_cents":4600000,"incoming_cents":incoming,"outgoing_cents":outgoing,
            "cash_cents":4600000+incoming-outgoing,"reserve_cents":1500000}


def finance_summary(tx):
    return summarize_cash_events(tx.events("cash"))


def cash_event(tx, key, entity_id, cents, direction, purpose):
    if isinstance(cents, bool) or not isinstance(cents, int) or cents < 0 or direction not in {"in","out"}:
        raise ValueError("模拟金额或收支方向无效")
    if direction == "out" and finance_summary(tx)["cash_cents"] - cents < 1500000:
        raise ValueError("模拟支出将突破15000元资金保留线，需先处理资金安排")
    tx.event(key, "cash", entity_id, {"cents":cents,"direction":direction,"purpose":purpose,"source":"simulation"})
