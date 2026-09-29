"""First-run seed records.

Every seed list is only inserted when its table is empty, so an existing
installation is never overwritten and no admin credential is ever changed
automatically.  All values can be overridden through the environment.
"""

from __future__ import annotations

import os

DEFAULT_ADMIN_EMAIL = "admin@dhatudrishti.local"
DEFAULT_ADMIN_NAME = "Administrator"
DEFAULT_ADMIN_PASSWORD = "ChangeMe#Admin2026"

# The prototype fleet that previously lived as hard-coded JavaScript on
# /machinery, mapped onto the persistent Machinery schema.
SEED_MACHINERY: list[dict] = [
    {
        "machine_id": "EXC-001",
        "name": "Hydraulic Excavator EX-210",
        "machine_type": "Excavator",
        "manufacturer": "Caterpillar",
        "model": "CAT 352",
        "mine_name": "Balaghat Mine (MOIL)",
        "status": "Operational",
        "year": 2026,
        "capacity": "3.2 m3 bucket",
        "description": "Primary overburden and ore excavation.",
    },
    {
        "machine_id": "DMP-002",
        "name": "Mining Dump Truck MT-40",
        "machine_type": "Dump Truck",
        "manufacturer": "Komatsu",
        "model": "HD605-7",
        "mine_name": "Ukwa Mine (MOIL)",
        "status": "Operational",
        "year": 2025,
        "capacity": "55 t payload",
        "description": "Main haulage route, pit to run-of-mine stockpile.",
    },
    {
        "machine_id": "LDL-003",
        "name": "Wheel Loader WL-30",
        "machine_type": "Wheel Loader",
        "manufacturer": "Caterpillar",
        "model": "966",
        "mine_name": "Chikla Mine (MOIL)",
        "status": "Operational",
        "year": 2026,
        "capacity": "3.5 yd3 bucket",
        "description": "Stockpile loading and material handling.",
    },
    {
        "machine_id": "EXC-004",
        "name": "Hydraulic Excavator EX-120",
        "machine_type": "Excavator",
        "manufacturer": "Hitachi",
        "model": "ZX470LC",
        "mine_name": "Dongri Buzurg Mine (MOIL)",
        "status": "Maintenance",
        "year": 2017,
        "capacity": "2.5 m3 bucket",
        "description": "High maintenance cost; hydraulic overhaul scheduled.",
    },
    {
        "machine_id": "DMP-005",
        "name": "Dump Truck DT-25",
        "machine_type": "Dump Truck",
        "manufacturer": "BharatBenz",
        "model": "3532R",
        "mine_name": "Balaghat Mine (MOIL)",
        "status": "Maintenance",
        "year": 2016,
        "capacity": "31 t payload",
        "description": "Under review for replacement.",
    },
    {
        "machine_id": "DRL-006",
        "name": "Blast Hole Drill BH-90",
        "machine_type": "Drill Rig",
        "manufacturer": "Epiroc",
        "model": "D65",
        "mine_name": "Gumgaon Mine (MOIL)",
        "status": "Inactive",
        "year": 2018,
        "capacity": "165 mm hole dia.",
        "description": "Stood down between blasting campaigns.",
    },
    {
        "machine_id": "DZR-007",
        "name": "Dozer DZ-100",
        "machine_type": "Dozer",
        "manufacturer": "Caterpillar",
        "model": "D8",
        "mine_name": "Ukwa Mine (MOIL)",
        "status": "Operational",
        "year": 2020,
        "capacity": "26 t operating weight",
        "description": "Haul road maintenance and bench cleanup.",
    },
    {
        "machine_id": "DMP-008",
        "name": "Mining Dump Truck MT-45",
        "machine_type": "Dump Truck",
        "manufacturer": "Volvo",
        "model": "A40G",
        "mine_name": "Chikla Mine (MOIL)",
        "status": "Operational",
        "year": 2025,
        "capacity": "40 t payload",
        "description": "Haulage support on the eastern dump.",
    },
]


def admin_email() -> str:
    return (os.environ.get("ADMIN_EMAIL") or DEFAULT_ADMIN_EMAIL).strip().lower()


def admin_name() -> str:
    return (os.environ.get("ADMIN_NAME") or DEFAULT_ADMIN_NAME).strip()


def admin_password() -> str:
    return os.environ.get("ADMIN_PASSWORD") or DEFAULT_ADMIN_PASSWORD


def using_default_password() -> bool:
    return "ADMIN_PASSWORD" not in os.environ or not admin_password().strip()
