import requests
from datetime import date, datetime, timedelta
import os
import io

AVAIL_LEVEL_NONE = 0
AVAIL_LEVEL_SEAT = 1
AVAIL_LEVEL_COUCHETTE = 2
AVAIL_LEVEL_PRIVATE_COUCHETTE = 3
AVAIL_LEVEL_BED = 4
AVAIL_LEVEL_PRIVATE_COUCHETTE_OR_BED = 5

level_mapping = {
    AVAIL_LEVEL_SEAT: {
        "sideCorridorCoach_2",
        "privateSeat",
        "centralGangwayCoachComfort_2",
        "centralGangwayCoachWithTableComfort_2",
        "serverlyDisabledPerson",
    },
    AVAIL_LEVEL_COUCHETTE: {
        "couchette4",
        "couchette6",
        "couchette4comfort",
        "femaleCouchette4",
        "femaleCouchette6",
        "femaleCouchette4comfort",
        "couchetteMiniSuite",
    },
    AVAIL_LEVEL_PRIVATE_COUCHETTE: {"privateCouchette", "privateCouchette4comfort"},
    AVAIL_LEVEL_BED: {
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
    def __init__(self) -> None:
        self.__session = requests.Session()
        self.__session.headers = {
            "Accept": "application/json",
            "Referer": "https://www.nightjet.com/de/ticket-buchen",
        }
        response = self.__session.post(
            "https://www.nightjet.com/nj-booking/init/start", json={"lang": "de"}
        )
        content = response.json()
        sessionCookie = response.cookies.get("SESSION")
        self.__session.cookies.set("SESSION", sessionCookie)
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
        stations = self.__session.get(
            f"https://www.nightjet.com/nj-booking/stations/find?lang=de&country=at&name={name}"
        )
        stations_json = stations.json()
        # find first non-meta
        target = None
        for station in stations_json:
            if station["name"] != "":
                target = station
                break

        if target is None:
            raise ValueError(f"Station {name} not found!")

        # print(f"Target: {repr(target)}")
        return (target["number"], target["name"])

    def findOffers(self, station_from, station_to, day: datetime.date, passengers):
        (station_from_id, _) = self.findStationId(station_from)
        (station_to_id, _) = self.findStationId(station_to)

        fmt_date = day.strftime("%d%m%Y")
        url_prefix = "https://www.nightjet.com/nj-booking/connection/find"
        url_suffix = "00:00?skip=0&limit=1&backward=false&lang=de"
        connections = self.__session.get(
            f"{url_prefix}/{station_from_id}/{station_to_id}/{fmt_date}/{url_suffix}"
        )
        connections_json = connections.json()
        connections_results = connections_json["results"]
        if len(connections_results) <= 0:
            return None
        first_connection_result = connections_results[0]
        target_train = first_connection_result["train"]
        departure_time = first_connection_result["from"]["dep_dt"]
        if datetime.fromtimestamp(departure_time / 1000).date() != day:
            return None

        jsonBody = {
            "njFrom": station_from_id,
            "njDep": departure_time,
            "njTo": station_to_id,
            "maxChanges": 0,
            "filter": {"njTrain": target_train, "njDeparture": departure_time},
            "objects": passengers,
            "relations": [],
            "lang": "de",
        }

        response = self.__session.post(
            "https://www.nightjet.com/nj-booking/offer/get", json=jsonBody
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
        avail_level = AVAIL_LEVEL_NONE
        for level, comp_identifier_set in level_mapping.items():
            if comp_identifier_set & set(flexschiene):
                if (
                    level == AVAIL_LEVEL_BED
                    and avail_level == AVAIL_LEVEL_PRIVATE_COUCHETTE
                ):
                    avail_level = AVAIL_LEVEL_PRIVATE_COUCHETTE_OR_BED
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
    advance_days=30,
    passengers=[],
):
    prefix = "output"
    os.makedirs(prefix, exist_ok=True)
    csv_out = f"{prefix}/{station_from}_{station_to}.csv"
    # TODO: add option to skip prices output
    csv_out_price_prefix = f"{prefix}/prices_{station_from}_{station_to}"

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
            line_time += "N;"
            if csv_out_price_prefix is not None:
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
            if csv_out_price_prefix is not None:
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

    if csv_out_price_prefix is not None:
        print("Outputting prices by category")
        for cat_type in avail_cat_types:
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
                csv_out_file_spar.write(";")
                csv_out_file_komf.write(";")
                csv_out_file_flex.write(";")

                for i in range(advance_days):
                    next_entry_spar = results_sparschiene[i]
                    next_entry_komfort = results_komfortschiene[i]
                    next_entry_flex = results_flexschiene[i]
                    csv_out_file_spar.write(f"{next_entry_spar.get(cat_type, 'N')};")
                    csv_out_file_komf.write(f"{next_entry_komfort.get(cat_type, 'N')};")
                    csv_out_file_flex.write(f"{next_entry_flex.get(cat_type, 'N')};")

                csv_out_file_spar.write("\n")
                csv_out_file_komf.write("\n")
                csv_out_file_flex.write("\n")

    init_file(filename=csv_out, header=line_init)
    with io.open(csv_out, "a") as csv_out_file:
        csv_out_file.write(f"{line_time}\n")


def main():
    jetter = Nightjetter()

    date_start = date(2024, 3, 15)
    station_from = "Berlin"
    station_to = "Paris"
    passengers = [
        {
            "type": "person",
            "gender": "male",
            "birthDate": "1993-06-16",
            "cards": [100000042],  # 100000042 = Klimaticket
        },
        {
            "type": "person",
            "gender": "female",
            "birthDate": "1993-06-16",
            "cards": [100000042],  # 100000042 = Klimaticket
        },
    ]

    protocol_connection(jetter, station_from, station_to, date_start, 30, passengers)
    # (wienID, _) = jetter.findStationId("Wien")
    # (hannoverID, _) = jetter.findStationId("Hannover")
    # print(json.dumps(jetter.findOffers("Wien", "Hannover", date(2023, 12, 20)), indent=2))


if __name__ == "__main__":
    main()
