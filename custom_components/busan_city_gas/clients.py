"""Construct a provider client without coupling the coordinator to authentication."""

from .portal import PortalClient


def create_client(session, data, provider, **kwargs):
    if provider.family == "energytalk":
        from .energytalk import EnergyTalkClient

        return EnergyTalkClient(session, data, provider)
    if provider.family == "daesung":
        from .daesung import DaesungClient

        return DaesungClient(session, data, provider)
    if provider.family == "haeyang":
        from .haeyang import HaeyangClient

        return HaeyangClient(session, data, provider)
    if provider.family == "gasapp":
        from .gasapp import GasappClient

        return GasappClient(session, data, provider)
    if provider.family == "samchully":
        from .samchully import SamchullyClient

        return SamchullyClient(session, data, provider)
    return PortalClient(session, data["username"], data["password"], provider, **kwargs)
