"""Read-only, manifest-driven ingestion of the seeded RBI source documents.

Milestone 2 scope: verify each official RBI page against the manifest, download
the linked PDF over allowlisted HTTPS GET, validate it, and store the raw bytes
immutably with SHA-256 metadata. Database persistence arrives in Milestone 4;
until then, metadata is stored as JSON next to each raw file.
"""
