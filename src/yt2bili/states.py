from enum import Enum


class State(Enum):
    DISCOVERED = "discovered"
    DOWNLOADED = "downloaded"
    EN_SUBTITLED = "en_subtitled"
    ZH_TRANSLATED = "zh_translated"
    BURNED = "burned"
    PENDING_REVIEW = "pending_review"
    READY = "ready"
    PUBLISHED = "published"
    SKIPPED_LONG = "skipped_long"
    SKIPPED = "skipped"

    @classmethod
    def failed(cls, stage: str) -> str:
        """Return a failed stage string (not an enum member)."""
        return f"failed_{stage}"
