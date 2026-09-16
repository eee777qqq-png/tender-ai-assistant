import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from quality_control import DiscrepancyLog


def test_log_and_retrieve_for_agent():
    log = DiscrepancyLog()
    log.log("agent_4_smetchik", "check-1", "unit_price", "1200", "1350")
    log.log("agent_4_smetchik", "check-1", "quantity", "10", "12")
    log.log("agent_3_analitik", "check-2", "customer_name", "ООО А", "ООО Б")

    smetchik_entries = log.for_agent("agent_4_smetchik")
    assert len(smetchik_entries) == 2
    assert log.for_agent("agent_3_analitik")[0].field == "customer_name"


def test_for_check_filters_by_check_id():
    log = DiscrepancyLog()
    log.log("agent_4_smetchik", "check-1", "unit_price", "1200", "1350")
    log.log("agent_4_smetchik", "check-2", "unit_price", "500", "550")

    entries = log.for_check("agent_4_smetchik", "check-1")
    assert len(entries) == 1
    assert entries[0].expert_value == "1350"


def test_most_common_fields_ranks_by_frequency():
    log = DiscrepancyLog()
    for i in range(3):
        log.log("agent_4_smetchik", f"check-{i}", "unit_price", "1", "2")
    log.log("agent_4_smetchik", "check-x", "quantity", "1", "2")

    top = log.most_common_fields("agent_4_smetchik", top_n=2)
    assert top[0] == ("unit_price", 3)
    assert top[1][0] == "quantity"


def test_critical_count():
    log = DiscrepancyLog()
    log.log("agent_4_smetchik", "check-1", "unit_price", "1", "2", critical=True)
    log.log("agent_4_smetchik", "check-2", "unit_price", "1", "2", critical=False)

    assert log.critical_count("agent_4_smetchik") == 1
    assert log.critical_count("agent_3_analitik") == 0
