import requests
import json
from datetime import date, datetime,timedelta
import os
import io

AVAIL_LEVEL_NONE = 0
AVAIL_LEVEL_SEAT = 1
AVAIL_LEVEL_COUCHETTE = 2
AVAIL_LEVEL_PRIVATE_COUCHETTE = 3
AVAIL_LEVEL_BED = 4
AVAIL_LEVEL_PRIVATE_COUCHETTE_OR_BED = 5


class Nightjetter:
    def __init__(self) -> None:
        self.__session = requests.Session()
        self.__session.headers = {
            "Accept": "application/json",
            "Referer": "https://www.nightjet.com/de/ticket-buchen"
        }
        response = self.__session.post(
            "https://www.nightjet.com/nj-booking/init/start",
            json={"lang": "de"}
        )
        content = response.json()
        sessionCookie = response.cookies.get("SESSION")
        self.__session.cookies.set("SESSION", sessionCookie)
        self.__session.headers["X-Public-ID"] = content["publicId"]

        # Get token
        response = self.__session.post(
            "https://www.nightjet.com/nj-booking/init/token",
            json={"action": "get", "lang": "de"}
        )
        content = response.json()
        self.__session.headers["CSRF-Token"] = content["CSRF-Token"]

    def findStationId(self, name):
        stations = self.__session.get(f"https://www.nightjet.com/nj-booking/stations/find?lang=de&country=at&name={name}") 
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

    def findOffers(self, station_from, station_to, day: datetime.date):        
        (station_from_id, _) = self.findStationId(station_from)
        (station_to_id, _) = self.findStationId(station_to)
        
        fmt_date = "%02d%02d%04d" % (day.day,day.month,day.year)
        connections = self.__session.get(f"https://www.nightjet.com/nj-booking/connection/find/{str(station_from_id)}/{str(station_to_id)}/{fmt_date}/00:00?skip=0&limit=1&backward=false&lang=de")
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
            "filter": {
                "njTrain": target_train,
                "njDeparture": departure_time
            },
            "objects": [
                {
                    "type": "person",
                    "gender": "male",
                    "birthDate": "1993-06-16",
                    "cards": [100000042] # 100000042 = Klimaticket
                }
            ],
            "relations": [],
            "lang": "de"
        }
        
        response = self.__session.post(
            "https://www.nightjet.com/nj-booking/offer/get",
            json=jsonBody
        )

        content = response.json()
        if "error" in content or content["result"][0] is None:
            return None

        first_result = content["result"][0]
        first_connection = first_result["connections"][0]
        return first_connection["offers"]
    
    def findOffersFiltered(self, station_from, station_to, day: datetime.date):
        offers = self.findOffers(station_from, station_to, day)
        if offers is None:
            return None
        
        sparschine = {}
        flexschine = {}
        for offer in offers:
            is_spar = False
            if "nightjetSparschiene" in offer["prodGroupLabels"]:
                is_spar = True
            elif "komfortticketStorno" not in offer["prodGroupLabels"]:
                continue
            
            compartments = offer["reservation"]["reservationSegments"][0]["compartments"]
            for compartment in compartments:
                comp_identifier = compartment["externalIdentifier"]
                # print(comp_identifier)
                compartment_object = None
                if "privateVariations" in compartment:
                    compartment_object = compartment["privateVariations"][0]["allocations"][0]["objects"]
                else:
                    compartment_object = compartment["objects"]
                total_price = 0
                for obj_entry in compartment_object:
                    total_price += obj_entry["price"]
                if is_spar:
                    sparschine[comp_identifier] = total_price
                else:
                    flexschine[comp_identifier] = total_price
        
        # Now calc avail level
        avail_level = AVAIL_LEVEL_NONE
        if "sideCorridorCoach_2" in flexschine or "privateSeat" in flexschine:
            avail_level = AVAIL_LEVEL_SEAT
        if "couchette4" in flexschine or "couchette6" in flexschine or "couchette4comfort" in flexschine or "femaleCouchette4" in flexschine or "femaleCouchette6" in flexschine or "femaleCouchette4comfort" in flexschine:
            avail_level = AVAIL_LEVEL_COUCHETTE
        if "privateCouchette" in flexschine or "privateCouchette4comfort" in flexschine:
            avail_level = AVAIL_LEVEL_PRIVATE_COUCHETTE
        if "single" in flexschine or "singleWithShowerWC" in flexschine or "double" in flexschine or "doubleWithShowerWC" in flexschine:
            if avail_level == AVAIL_LEVEL_PRIVATE_COUCHETTE:
                avail_level = AVAIL_LEVEL_PRIVATE_COUCHETTE_OR_BED
            else:
                avail_level = AVAIL_LEVEL_BED
        return (avail_level, sparschine, flexschine)

# Protocol some days
def protocol_connection(jetter: Nightjetter, station_from, station_to, csv_out, date_start, advance_days=30, csv_out_price_prefix=None):
    (_, station_from_resl_name) = jetter.findStationId(station_from)
    (_, station_to_resl_name) = jetter.findStationId(station_to)

    line_init = ";"
    line_time = str(datetime.now()) + ";"

    results_sparschiene = []
    results_flexschiene = []
    avail_cat_types = set()


    for i in range(advance_days):   
        next_date = date_start + timedelta(days=i)
        line_init += str(next_date) + ";"
        offers = jetter.findOffersFiltered(station_from, station_to, next_date)
        if offers is None:
            line_time += "N" + ";"
            if csv_out_price_prefix is not None:
                results_sparschiene.append({})
                results_flexschiene.append({})
        else:
            (avail_level, sparschine, flexschine) = offers # TODO: protocol prices
            if csv_out_price_prefix is not None:
                results_sparschiene.append(sparschine)
                results_flexschiene.append(flexschine)
                for cat_type in sparschine:
                    avail_cat_types.add(cat_type)
                for cat_type in flexschine:
                    avail_cat_types.add(cat_type)
            line_time += str(avail_level) + ";"
        print("Processing connection from ", station_from_resl_name , " to ", station_to_resl_name ," at", str(next_date))
    
    print("Outputting prices by category:")
    if csv_out_price_prefix is not None:
        for cat_type in avail_cat_types:
            fname_sparschiene = csv_out_price_prefix + "-" + cat_type + "-spar.csv"
            fname_flexschiene = csv_out_price_prefix + "-" + cat_type + "-flex.csv"

            if not os.path.exists(fname_sparschiene):
                with io.open(fname_sparschiene, "w") as csv_out_file:
                    csv_out_file.write(line_init + "\n")

            if not os.path.exists(fname_flexschiene):
                with io.open(fname_flexschiene, "w") as csv_out_file:
                    csv_out_file.write(line_init + "\n")

            with io.open(fname_sparschiene, "a") as csv_out_file_spar:
                with io.open(fname_flexschiene, "a") as csv_out_file_flex:
                    csv_out_file_spar.write(";")
                    csv_out_file_flex.write(";")
                    
                    for i in range(advance_days):
                        next_entry_sparschiene = results_sparschiene[i]
                        next_entry_flexschiene = results_flexschiene[i]
                        if cat_type in next_entry_sparschiene:
                            csv_out_file_spar.write(str(next_entry_sparschiene[cat_type]) + ";")
                        else:
                            csv_out_file_spar.write("N" + ";")
                        if cat_type in next_entry_flexschiene:
                            csv_out_file_flex.write(str(next_entry_flexschiene[cat_type]) + ";")
                        else:
                            csv_out_file_flex.write("N" + ";")
                        
                    
                    csv_out_file_spar.write("\n")
                    csv_out_file_flex.write("\n")

    if not os.path.exists(csv_out):
        with io.open(csv_out, "w") as csv_out_file:
            csv_out_file.write(line_init + "\n")

    with io.open(csv_out, "a") as csv_out_file:
        csv_out_file.write(line_time + "\n")


def main():
    jetter = Nightjetter()

    date_start = date(2023, 9, 30)
    protocol_connection(jetter, "Wien", "Hannover", "wien_hannover.csv", date_start, 90, "prices_wien_hannover")
    protocol_connection(jetter, "Hannover", "Wien", "hannover_wien.csv", date_start, 90, "prices_hannover_wien")
    # jetter.findStationId("Wien")
    # jetter.findStationId("Hannover")
    # print(json.dumps(jetter.findOffers("Hannover-Wien", date(2023, 8, 26)), indent=2))

    


if __name__ == '__main__':
    main()