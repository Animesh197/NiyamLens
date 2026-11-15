"""Load and validate the seed source manifest."""

import hashlib
from datetime import date
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs, urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

DEFAULT_MANIFEST_PATH = Path(__file__).with_name("source_manifest.yaml")

# Hosts permitted by implementationplan.md §2.3. A manifest may narrow this
# list per record but can never widen it.
PLAN_ALLOWED_HOSTS = frozenset({"www.rbi.org.in", "rbi.org.in", "rbidocs.rbi.org.in"})

KEY_PATTERN = r"^[a-z0-9][a-z0-9_]{2,79}$"


class ManifestError(ValueError):
    """The manifest file is missing, malformed, or violates a safety rule."""


def check_url(url: str, allowed_hosts: frozenset[str] | set[str] | list[str]) -> None:
    """Raise ValueError unless ``url`` is an https URL on an allowed host."""
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise ValueError(f"URL must use https: {url}")
    if parts.username or parts.password:
        raise ValueError(f"URL must not contain credentials: {url}")
    host = (parts.hostname or "").lower()
    if host not in {h.lower() for h in allowed_hosts}:
        raise ValueError(f"URL host '{host}' is not in the allowlist: {url}")
    if parts.port not in (None, 443):
        raise ValueError(f"URL must use the default https port: {url}")


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FamilyRecord(_Strict):
    family_key: str = Field(pattern=KEY_PATTERN)
    official_title: str = Field(min_length=10)
    regulator: str
    jurisdiction: str
    rbi_direction_id: int = Field(gt=0)
    previous_versions_url: str
    comparison_eligible: bool
    comparison_note: str

    @model_validator(mode="after")
    def _check(self) -> "FamilyRecord":
        check_url(self.previous_versions_url, PLAN_ALLOWED_HOSTS)
        return self


class SourceRecord(_Strict):
    source_key: str = Field(pattern=KEY_PATTERN)
    document_family_key: str = Field(pattern=KEY_PATTERN)
    version_role: Literal["historical", "current"]
    expected_title: str = Field(min_length=10)
    source_page_url: str
    expected_issue_date: date
    expected_updated_as_on: date | None
    expected_history_entry_date: date | None
    expected_document_identifiers: list[str]
    expected_pdf_url: str
    allowed_hosts: list[str] = Field(min_length=1)
    comparison_eligible: bool
    notes: str = ""

    @model_validator(mode="after")
    def _check(self) -> "SourceRecord":
        widened = {h.lower() for h in self.allowed_hosts} - PLAN_ALLOWED_HOSTS
        if widened:
            raise ValueError(f"allowed_hosts may not add hosts beyond the plan: {sorted(widened)}")
        check_url(self.source_page_url, self.allowed_hosts)
        check_url(self.expected_pdf_url, self.allowed_hosts)
        if self.version_role == "historical":
            if self.expected_history_entry_date is None:
                raise ValueError("historical sources need expected_history_entry_date")
            if self.history_id is None:
                raise ValueError("historical source_page_url must include a Histid parameter")
            if self.expected_updated_as_on is not None:
                raise ValueError("historical sources must not set expected_updated_as_on")
        elif self.expected_history_entry_date is not None:
            raise ValueError("current sources must not set expected_history_entry_date")
        return self

    @property
    def history_id(self) -> str | None:
        """The RBI ``Histid`` query value of a history page, if any."""
        query = {k.lower(): v for k, v in parse_qs(urlsplit(self.source_page_url).query).items()}
        values = query.get("histid")
        return values[0] if values else None


class SourceManifest(_Strict):
    manifest_version: Literal[1]
    researched_on: date
    rbi_hosts: list[str]
    families: list[FamilyRecord] = Field(min_length=1)
    sources: list[SourceRecord] = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self) -> "SourceManifest":
        family_keys = [f.family_key for f in self.families]
        source_keys = [s.source_key for s in self.sources]
        for label, keys in (("family_key", family_keys), ("source_key", source_keys)):
            duplicates = sorted({k for k in keys if keys.count(k) > 1})
            if duplicates:
                raise ValueError(f"duplicate {label}: {duplicates}")
        families = {f.family_key: f for f in self.families}
        for source in self.sources:
            family = families.get(source.document_family_key)
            if family is None:
                raise ValueError(
                    f"{source.source_key}: unknown document_family_key "
                    f"'{source.document_family_key}'"
                )
            if source.comparison_eligible and not family.comparison_eligible:
                raise ValueError(
                    f"{source.source_key}: family '{family.family_key}' is not comparison-eligible"
                )
        return self

    def family(self, family_key: str) -> FamilyRecord:
        for family in self.families:
            if family.family_key == family_key:
                return family
        raise KeyError(family_key)


def load_manifest(path: Path = DEFAULT_MANIFEST_PATH) -> tuple[SourceManifest, str]:
    """Load and validate the manifest. Returns the manifest and the file's SHA-256."""
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ManifestError(f"Cannot read source manifest at {path}: {exc}") from exc
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ManifestError(f"Source manifest is not valid YAML: {exc}") from exc
    try:
        manifest = SourceManifest.model_validate(data)
    except ValidationError as exc:
        raise ManifestError(f"Source manifest failed validation:\n{exc}") from exc
    return manifest, hashlib.sha256(raw).hexdigest()
