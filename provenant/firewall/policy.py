"""Owner request -> just-in-time Policy. Owner: Person 2.
Rule 1: requester != owner -> Policy.deny_all(...)
Rule 2: addresses come ONLY from trusted records (provenant.records), never from content.
"""
from ..schemas import Policy


def make_policy(request: str, requester: str, owner_id: str) -> Policy:
    raise NotImplementedError("Person 2")
