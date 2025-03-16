from dataclasses import dataclass, field
from enum import IntEnum, StrEnum
import requests
from datetime import date, datetime, timedelta
import os
import io


class AvailLevel(StrEnum):
    NONE = "None"
    SEAT = "Seat"
    COUCHETTE = "Couchette"
    PRIVATE_COUCHETTE = "Private_Couchette"
    BED = "Bed"
    PRIVATE_COUCHETTE_OR_BED = "Private_Couchette_Or_Bed"


LEVEL_MAPPING = {
    AvailLevel.SEAT: {
        "sideCorridorCoach_2",
        "privateSeat",
        "centralGangwayCoachComfort_2",
        "centralGangwayCoachWithTableComfort_2",
        "serverlyDisabledPerson",
    },
    AvailLevel.COUCHETTE: {
        "couchette4",
        "couchette6",
        "couchette4comfort",
        "femaleCouchette4",
        "femaleCouchette6",
        "femaleCouchette4comfort",
        "couchetteMiniSuite",
    },
    AvailLevel.PRIVATE_COUCHETTE: {"privateCouchette", "privateCouchette4comfort"},
    AvailLevel.BED: {
        "single",
        "singleWithShowerWC",
        "double",
        "doubleWithShowerWC",
        "singleComfort",
        "doubleComfort",
        "singleComfortPlus",
        "doubleComfortPlus",
    },
}


class Nightjetter:
    # TODO: This could be set dynamically by parsing the HTML of the "ticket-buchen"
    # and read the value of div.nj-root.data-nj-base-url
    API_URL_BASE = "https://www.nightjet.com/nj-booking-ocp"

    def __init__(self) -> None:
        self.__session = requests.Session()
        self.__session.headers = {
            "Accept": "application/json",
            "Referer": "https://www.nightjet.com/de/ticket-buchen",
        }
        response = self.__session.post(
            f"{self.API_URL_BASE}/init/start", json={"lang": "de"}
        )
        # sessioncookie is currently useless
        sessionCookie = response.cookies.get("SESSION")
        self.__session.cookies.set("SESSION", sessionCookie)

        content = response.json()
        self.__session.headers["X-Public-ID"] = content["publicId"]
        self.__session.headers["X-Token"] = content["token"]

        # Get token
        # response = self.__session.post(
        #     "https://www.nightjet.com/nj-booking/init/token",
        #     json={"action": "get", "lang": "de"}
        # )
        # content = response.json()
        # self.__session.headers["CSRF-Token"] = content["CSRF-Token"]

    def findStationId(self, name):
        # Parameter t was added, probably expects realistic since_epoch timestamp
        # but for now works with whatever value
        stations = self.__session.get(
            f"{self.API_URL_BASE}/stations/find?lang=de&country=at&name={name}&t=1"
        )
        stations_json = stations.json()
        # find first non-meta
        target = None
        for station in stations_json:
            if station["name"]:
                target = station
                break

        if target is None:
            raise ValueError(f"Station {name} not found!")

        # print(f"Target: {repr(target)}")
        return (target["number"], target["name"])

    def findStationIdMeta(self, name):
        # Parameter t was added, probably expects realistic since_epoch timestamp
        # but for now works with whatever value
        stations = self.__session.get(
            f"{self.API_URL_BASE}/stations/find?lang=de&country=at&name={name}&t=1"
        )
        stations_json = stations.json()

        # find first meta
        target = None
        for station in stations_json:
            if station["meta"]:
                target = station
                break

        if target is None:
            raise ValueError(f"Station {name} not found!")

        # print(f"Target: {repr(target)}")
        return (target["number"], target["meta"])

    def findOffers(self, station_from, station_to, day: datetime.date, passengers):
        (station_from_id, _) = self.findStationId(station_from)
        (station_to_id, _) = self.findStationId(station_to)

        (station_from_id_meta, _) = self.findStationIdMeta(station_from)
        (station_to_id_meta, _) = self.findStationIdMeta(station_to)

        fmt_date = day.strftime("%Y-%m-%d")
        url_prefix = f"{self.API_URL_BASE}/connection"
        # url_suffix = "00:00?skip=0&limit=1&backward=false&lang=de"
        # conn = f"{url_prefix}/{station_from_id}/{station_to_id}/{fmt_date}"
        connections = self.__session.get(
            f"{url_prefix}/{station_from_id_meta}/{station_to_id_meta}/{fmt_date}"
        )
        connections_json = connections.json()
        connections_results = connections_json["connections"]
        if len(connections_results) <= 0:
            return None
        first_connection_result = connections_results[0]
        target_train = first_connection_result["trains"][0]
        target_train_name = target_train["train"]
        departure_time = target_train["departure"]["utc"]
        if datetime.fromtimestamp(departure_time / 1000).date() != day:
            return None

        jsonBody = {
            "njFrom": station_from_id,
            "njDep": departure_time,
            "njTo": station_to_id,
            "maxChanges": 0,
            "filter": {"njTrain": target_train_name, "njDeparture": departure_time},
            "objects": passengers,
            "relations": [],
            "lang": "de",
        }

        response = self.__session.post(
            f"{self.API_URL_BASE}/offer/get", json=jsonBody
        )

        content = response.json()
        if "error" in content or content["result"][0] is None:
            return None

        first_result = content["result"][0]
        first_connection = first_result["connections"][0]
        return first_connection["offers"]

    def findOffersFiltered(
        self, station_from, station_to, day: datetime.date, passengers
    ):
        offers = self.findOffers(station_from, station_to, day, passengers)
        if offers is None:
            return None

        print(f"Seats available from {station_from} to {station_to} on {day}")

        sparschiene = {}
        komfortschiene = {}
        flexschiene = {}
        for offer in offers:
            is_spar = False
            is_komfort = False
            if "Kein Storno" in offer["prodGroupLabels"]:
                is_spar = True
            elif "komfortticketStorno" in offer["prodGroupLabels"]:
                is_komfort = True
            elif "Vollstorno" not in offer["prodGroupLabels"]:
                continue

            reservation_segments = offer["reservation"]["reservationSegments"]
            compartments = reservation_segments[0]["compartments"]
            for compartment in compartments:
                comp_identifier = compartment["externalIdentifier"]
                # print(comp_identifier)
                compartment_object = None
                if "privateVariations" in compartment:
                    allocations = compartment["privateVariations"][0]["allocations"]
                    compartment_object = allocations[0]["objects"]
                else:
                    compartment_object = compartment["objects"]
                total_price = 0
                for obj_entry in compartment_object:
                    total_price += obj_entry["price"]
                if is_spar:
                    sparschiene[comp_identifier] = total_price
                elif is_komfort:
                    komfortschiene[comp_identifier] = total_price
                else:
                    flexschiene[comp_identifier] = total_price

        # Now calc avail level
        avail_level = AvailLevel.NONE
        for level, comp_identifier_set in LEVEL_MAPPING.items():
            if comp_identifier_set & set(flexschiene):
                if (
                    level == AvailLevel.BED
                    and avail_level == AvailLevel.PRIVATE_COUCHETTE
                ):
                    avail_level = AvailLevel.PRIVATE_COUCHETTE_OR_BED
                else:
                    avail_level = level
        return (avail_level, sparschiene, komfortschiene, flexschiene)


def init_file(filename: str, header: str) -> None:
    if not os.path.exists(filename):
        with io.open(filename, "w") as csv_out_file:
            csv_out_file.write(f"{header}\n")


# Protocol some days
def protocol_connection(
    jetter: Nightjetter,
    station_from,
    station_to,
    date_start,
    advance_days=7,
    passengers=[],
):
    prefix = "output"
    os.makedirs(prefix, exist_ok=True)
    filename = f"{station_from}_{station_to}_{len(passengers)}PAX_{date_start}"
    csv_out = f"{prefix}/{filename}.csv"
    # TODO: add option to skip prices output
    csv_out_price_prefix = f"{prefix}/prices_{filename}"

    (_, station_from_resl_name) = jetter.findStationId(station_from)
    (_, station_to_resl_name) = jetter.findStationId(station_to)

    line_init = ";"
    line_time = f"{datetime.now()};"

    results_sparschiene = []
    results_komfortschiene = []
    results_flexschiene = []
    avail_cat_types = set()

    for i in range(advance_days):
        next_date = date_start + timedelta(days=i)
        line_init += f"{next_date};"
        offers = jetter.findOffersFiltered(
            station_from, station_to, next_date, passengers
        )
        if offers is None:
            line_time += "None;"
            if csv_out_price_prefix:
                results_sparschiene.append({})
                results_komfortschiene.append({})
                results_flexschiene.append({})
        else:
            (
                avail_level,
                sparschiene,
                komfortschiene,
                flexschiene,
            ) = offers  # TODO: protocol prices
            if csv_out_price_prefix:
                results_sparschiene.append(sparschiene)
                results_komfortschiene.append(komfortschiene)
                results_flexschiene.append(flexschiene)
                avail_cat_types.update(sparschiene.keys())
                avail_cat_types.update(komfortschiene.keys())
                avail_cat_types.update(flexschiene.keys())
            line_time += f"{avail_level};"
        print(
            f"Processing connection from {station_from_resl_name} to {station_to_resl_name} at {next_date}"
        )

    if csv_out_price_prefix and avail_cat_types:
        print("Outputting prices by category")
        for cat_type in avail_cat_types:
            spar_offers = [str(offer.get(cat_type)) for offer in results_sparschiene]
            komf_offers = [str(offer.get(cat_type)) for offer in results_komfortschiene]
            flex_offers = [str(offer.get(cat_type)) for offer in results_flexschiene]

            fname_sparschiene = f"{csv_out_price_prefix}-{cat_type}-spar.csv"
            fname_komfortschiene = f"{csv_out_price_prefix}-{cat_type}-komf.csv"
            fname_flexschiene = f"{csv_out_price_prefix}-{cat_type}-flex.csv"

            for fname in (fname_flexschiene, fname_komfortschiene, fname_sparschiene):
                init_file(filename=fname, header=line_init)

            # Python 3.10+ only syntax
            with (
                io.open(fname_sparschiene, "a") as csv_out_file_spar,
                io.open(fname_komfortschiene, "a") as csv_out_file_komf,
                io.open(fname_flexschiene, "a") as csv_out_file_flex,
            ):
                csv_out_file_spar.write(f";{(';').join(spar_offers)}\n")
                csv_out_file_komf.write(f";{(';').join(komf_offers)}\n")
                csv_out_file_flex.write(f";{(';').join(flex_offers)}\n")

    init_file(filename=csv_out, header=line_init)
    with io.open(csv_out, "a") as csv_out_file:
        csv_out_file.write(f"{line_time}\n")


TODAY = date.today()


class AgeGroup(StrEnum):
    """
    AgeGroup enumerating possibilities and to which birthDate they are computed

    ADULT_15_99: Age 15 and more
    KID_10_14: Age 10 to 14
    KID_6_9: Age 6 to 9
    SMALL_KID_0_5: Age 0 to 5

    There are since 2024 two kid categories in the frontend, which currently translate
    to the same kid category and the same price, but this might change in the future.
    """

    ADULT_15_99 = TODAY.replace(year=TODAY.year - 30).isoformat()
    KID_10_14 = TODAY.replace(year=TODAY.year - 10).isoformat()
    KID_6_9 = TODAY.replace(year=TODAY.year - 10).isoformat()
    SMALL_KID_0_5 = TODAY.isoformat()


class Gender(StrEnum):
    MALE = "male"
    FEMALE = "female"
    DIVERSE = "diverse"


# Many more availables, but here probably the most important ones
class ReductionCard(IntEnum):
    DB_BAHNCARD_25_2KL = 127
    DB_BAHNCARD_50_2KL = 129
    DB_TICKET_DEUTSCHLAND_2KL = 9098153
    KLIMATICKET = 100000042


@dataclass
class Passenger:
    gender: Gender
    age_group: AgeGroup
    reduction_cards: list[ReductionCard] = field(default_factory=list)

    def to_dict(self):
        return {
            "type": "person",
            "gender": self.gender,
            "birthDate": self.age_group,
            "cards": self.reduction_cards,
        }


FE = Passenger(Gender.MALE, AgeGroup.SMALL_KID_0_5, [])
LI = Passenger(Gender.MALE, AgeGroup.SMALL_KID_0_5, [])
MY = Passenger(Gender.FEMALE, AgeGroup.ADULT_15_99, [ReductionCard.DB_BAHNCARD_25_2KL])
MA = Passenger(Gender.MALE, AgeGroup.ADULT_15_99, [ReductionCard.DB_BAHNCARD_25_2KL])


@dataclass
class Connection:
    station_from: str
    station_to: str
    date_start: date
    advance_days: int
    passengers: list[Passenger]

    def to_kwargs(self) -> dict:
        return {
            "station_from": self.station_from,
            "station_to": self.station_to,
            "date_start": self.date_start,
            "advance_days": self.advance_days,
            "passengers": [passenger.to_dict() for passenger in self.passengers],
        }

CONNECTIONS = [
    Connection(
        station_from="Paris",
        station_to="Berlin",
        date_start=date(2025, 4, 14),
        advance_days=4,
        passengers=[MA, LI, FE],
    ),
    Connection(
        station_from="Berlin",
        station_to="Paris",
        date_start=date(2025, 4, 27),
        advance_days=2,
        passengers=[MY],
    ),
    Connection(
        station_from="Paris",
        station_to="Berlin",
        date_start=date(2025, 4, 1),
        advance_days=3,
        passengers=[MY],
    ),
]


def main():
    jetter = Nightjetter()

    for connection in CONNECTIONS:
        protocol_connection(jetter, **connection.to_kwargs())


if __name__ == "__main__":
    main()
