import time
import aiosqlite

from config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL,
    username    TEXT,
    frm         TEXT NOT NULL,
    to_cur      TEXT NOT NULL,
    amount_in   REAL NOT NULL,
    amount_out  REAL NOT NULL,
    rate        REAL NOT NULL,
    payout_addr TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'new',  -- new, paid, done, rejected, cancelled
    created_at  INTEGER NOT NULL,
    expires_at  INTEGER NOT NULL
);
"""


async def init():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(SCHEMA)
        await db.commit()


async def create_order(user_id, username, frm, to_cur, amount_in, amount_out,
                       rate, payout_addr, ttl_min) -> int:
    now = int(time.time())
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO orders (user_id, username, frm, to_cur, amount_in, amount_out,"
            " rate, payout_addr, created_at, expires_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (user_id, username, frm, to_cur, amount_in, amount_out, rate,
             payout_addr, now, now + ttl_min * 60),
        )
        await db.commit()
        return cur.lastrowid


async def get_order(order_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM orders WHERE id = ?", (order_id,))
        return await cur.fetchone()


async def set_status(order_id: int, status: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE orders SET status = ? WHERE id = ?", (status, order_id))
        await db.commit()


async def user_orders(user_id: int, limit: int = 10):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM orders WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit))
        return await cur.fetchall()


async def stats():
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT status, COUNT(*) FROM orders GROUP BY status")
        return dict(await cur.fetchall())
