"""Persist successful entry receipts in the same transaction as the ledger write."""
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sqlite3

from fastapi import HTTPException

from app.database import connect, create_backup


@dataclass
class Submission:
    connection: sqlite3.Connection | None = None
    backup: Path | None = None
    replay: dict | None = None
    result: dict | None = None


def lookup(connection, request_id, operation, fingerprint):
    row = connection.execute("SELECT * FROM entry_requests WHERE request_id=?", (request_id,)).fetchone()
    if row is None:
        return None
    if row["operation"] != operation or row["fingerprint"] != fingerprint:
        raise HTTPException(409, detail=dict(
            code="entry_request_conflict",
            message="这次提交的内容与已保存记录不一致，请先在流水中核对。",
        ))
    return {**json.loads(row["response_json"]), "replayed": True}


@contextmanager
def entry_submission(operation, payload):
    request_id = str(payload.request_id) if payload.request_id is not None else None
    fingerprint = None
    if request_id:
        body = payload.model_dump(mode="json", exclude={"request_id"})
        encoded = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        fingerprint = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        with connect() as connection:
            replay = lookup(connection, request_id, operation, fingerprint)
        if replay is not None:
            yield Submission(replay=replay)
            return

    backup = create_backup()
    with connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        if request_id:
            # Another request may have committed while this one was making its backup.
            replay = lookup(connection, request_id, operation, fingerprint)
            if replay is not None:
                yield Submission(replay=replay)
                return
        submission = Submission(connection=connection, backup=backup)
        yield submission
        if request_id:
            if submission.result is None:
                raise RuntimeError("记账没有生成保存回执，已回滚。")
            connection.execute("""INSERT INTO entry_requests(request_id,operation,fingerprint,response_json)
                VALUES (?,?,?,?)""", (request_id, operation, fingerprint,
                                       json.dumps(submission.result, ensure_ascii=False)))
