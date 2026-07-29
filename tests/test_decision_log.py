"""Decision log: append-only, hash-chained, tamper-evident (spec Phase 7)."""
import json

from daybreak.report.decision_log import DecisionLog


def test_chain_valid_and_tamper_evident(tmp_path):
    log = DecisionLog(tmp_path)
    log.append({"decision": "CASH", "note": "day 1"})
    log.append({"decision": "TRADE", "note": "day 2"})
    log.append({"decision": "HOLD", "note": "day 3"})
    assert log.verify_chain()

    # tamper with the middle record -> chain must break
    lines = log.path.read_text().splitlines()
    rec = json.loads(lines[1])
    rec["decision"] = "CASH"
    lines[1] = json.dumps(rec, sort_keys=True, default=str)
    log.path.write_text("\n".join(lines) + "\n")
    assert not log.verify_chain()
