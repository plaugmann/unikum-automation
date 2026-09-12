"""Klient mod Unikums GraphQL-API.

Endpointet og felterne er kortlagt i docs/unikum-api.md. Introspektion er
slaaet fra paa serveren, saa queries herunder er skrevet ud fra det skema,
frontenden selv baerer rundt paa.
"""
from __future__ import annotations

from typing import Any, Iterator

import httpx

from . import auth, config

HEADERS = {
    "X-App-Origin": "Monolith",
    "X-Language": "sv",
    "Content-Type": "application/json",
    "Accept": "*/*",
}

# Listen udfylder hverken 'attachments' eller 'owner' - det kraever et
# detaljekald pr. post. Vi henter derfor kun det, der skal til for at
# afgoere, om vi allerede kender posten.
LIST_QUERY = """
query Feed($pid: ID!, $first: Int!, $cursor: String, $from: Date) {
  User(id: $pid) {
    informationEntriesByRole(
      role: GUARDIAN, sortOn: PUBLISHED, isUsersOwnPage: true,
      first: $first, after: $cursor, from: $from
    ) {
      pageInfo { hasNextPage endCursor }
      edges { node {
        _id title informationType published lastEdited important
        author { firstName lastName }
      } }
    }
  }
}
"""

ENTRY_QUERY = """
query Entry($id: ID!) {
  InformationEntry(id: $id) {
    _id title body published lastEdited informationType important
    from to recipients owningSchoolId
    author { firstName lastName }
    owner {
      __typename
      ... on School { _id fullName }
      ... on Group { _id fullName }
      ... on User { _id firstName lastName }
    }
    recipientContexts { edges { node {
      __typename
      ... on School { _id fullName }
      ... on Group { _id fullName }
    } } }
    attachments { _id path }
  }
}
"""

PLANNINGS_QUERY = """
query Plannings($pid: ID!, $first: Int!, $cursor: String) {
  User(id: $pid) {
    plannings(first: $first, after: $cursor) {
      pageInfo { hasNextPage endCursor }
      edges { node {
        _id title ingress body lastEdited
        owner { __typename ... on Group { _id fullName } ... on School { _id fullName } }
        owningSchool { _id fullName }
        attachments { _id path }
      } }
    }
  }
}
"""


class UnikumError(RuntimeError):
    pass


class Client:
    """Tynd GraphQL-klient der selv sikrer, at tokenet er friskt."""

    def __init__(self, *, allow_login: bool = False) -> None:
        self._allow_login = allow_login
        self._http = httpx.Client(timeout=60.0)

    def __enter__(self) -> "Client":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def _headers(self) -> dict[str, str]:
        token = auth.get_access_token(allow_login=self._allow_login)
        return {**HEADERS, "Authorization": f"Bearer {token}"}

    def query(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        payload = {"query": query, "variables": variables}
        res = self._http.post(config.GRAPHQL_URL, json=payload, headers=self._headers())

        # Et udloebet token giver 401. Auth-laget fornyer ved naeste kald, saa
        # et enkelt forsoeg mere er nok.
        if res.status_code == 401:
            config.TOKEN_CACHE.unlink(missing_ok=True)
            res = self._http.post(config.GRAPHQL_URL, json=payload, headers=self._headers())

        if res.status_code != 200:
            raise UnikumError(f"HTTP {res.status_code}: {res.text[:300]}")

        body = res.json()
        if body.get("errors"):
            msgs = "; ".join(e.get("message", "?") for e in body["errors"])
            raise UnikumError(f"GraphQL-fejl: {msgs}")
        return body["data"]

    # --- Beskeder ---------------------------------------------------------

    def list_entries(self, since: str | None = None, page_size: int = 100) -> Iterator[dict]:
        """Alle beskeder fra og med 'since', nyeste foerst.

        'from' filtreres serverside, saa vi henter ikke hele skolehistorikken
        for at smide den vaek igen.
        """
        cursor = ""
        while True:
            data = self.query(
                LIST_QUERY,
                {
                    "pid": config.GUARDIAN_PID,
                    "first": page_size,
                    "cursor": cursor,
                    "from": since or config.SINCE,
                },
            )
            conn = data["User"]["informationEntriesByRole"]
            for edge in conn["edges"]:
                yield edge["node"]
            if not conn["pageInfo"]["hasNextPage"]:
                return
            cursor = conn["pageInfo"]["endCursor"]

    def get_entry(self, entry_id: str) -> dict:
        return self.query(ENTRY_QUERY, {"id": str(entry_id)})["InformationEntry"]

    # --- Pedagogiska planeringar ------------------------------------------

    def list_plannings(self, page_size: int = 50) -> Iterator[dict]:
        cursor = ""
        while True:
            data = self.query(
                PLANNINGS_QUERY,
                {"pid": config.CHILD_PID, "first": page_size, "cursor": cursor},
            )
            conn = data["User"]["plannings"]
            for edge in conn["edges"]:
                yield edge["node"]
            if not conn["pageInfo"]["hasNextPage"]:
                return
            cursor = conn["pageInfo"]["endCursor"]

    # --- Bilag ------------------------------------------------------------

    def download_attachment(self, path: str) -> bytes:
        """Hent et bilag.

        'path' er som API'et leverer det, fx /content/4cc0/<id>-<uuid>.pdf.
        Bemaerk at filerne ligger under /unikum/content + path, saa "content"
        optraeder to gange i den faerdige URL.
        """
        url = f"{config.BASE}/unikum/content{path}"
        res = self._http.get(url, headers=self._headers(), follow_redirects=True)
        if res.status_code != 200:
            raise UnikumError(f"Kunne ikke hente bilag ({res.status_code}): {url}")
        return res.content
