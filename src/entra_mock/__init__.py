"""Mock Microsoft Graph — appartenance aux groupes Entra.

N'est PAS une source BoondManager : autre authentification (OAuth2 client
credentials), autre enveloppe (`value[]` + `@odata.nextLink`), autre contrat.
Cf. docs/SPEC-DEVIATIONS.md #4.
"""

from .app import app

__all__ = ["app"]
