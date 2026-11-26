from app.connectors.base import Connector, JobIn, SearchPage
from app.connectors.dealls import DeallsConnector
from app.connectors.glints import GlintsConnector
from app.connectors.instagram import InstagramConnector
from app.connectors.googlejobs import GoogleJobsConnector
from app.connectors.jobstreet import JobStreetConnector
from app.connectors.kalibrr import KalibrrConnector
from app.connectors.karirhub import KarirhubConnector
from app.connectors.kitalulus import KitaLulusConnector
from app.connectors.linkedin import LinkedInConnector
from app.connectors.lokerid import LokerIdConnector
from app.connectors.traveloka import TravelokaConnector

# Tambah sumber baru di sini — otomatis ikut scraping terjadwal & tombol manual.
REGISTRY: dict[str, Connector] = {
    c.name: c
    for c in (
        JobStreetConnector(),
        KalibrrConnector(),
        GlintsConnector(),
        DeallsConnector(),
        LokerIdConnector(),
        KitaLulusConnector(),
        TravelokaConnector(),
        LinkedInConnector(),
        KarirhubConnector(),
        GoogleJobsConnector(),
        InstagramConnector(),
    )
}

__all__ = ["REGISTRY", "Connector", "JobIn", "SearchPage"]
