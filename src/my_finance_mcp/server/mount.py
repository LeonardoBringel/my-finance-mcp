"""Mount at "/" that only matches the paths the mounted app declares."""

from starlette._utils import get_route_path
from starlette.applications import Starlette
from starlette.routing import Match, Mount
from starlette.types import Scope


class McpMount(Mount):
    # A plain Mount("/") matches every path, which would turn the app's 404
    # and 405 envelopes into the sub-app's own responses. Declining unknown
    # paths keeps them with the main router.
    def __init__(self, app: Starlette) -> None:
        super().__init__("/", app=app)
        self._paths = frozenset(route.path for route in app.routes)

    def matches(self, scope: Scope) -> tuple[Match, Scope]:
        if get_route_path(scope) not in self._paths:
            return Match.NONE, {}
        return super().matches(scope)
