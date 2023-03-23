# pylint: disable=too-many-arguments,too-many-locals,
import itertools
import json
import os
import random
import sys
import time
import traceback
import warnings
from enum import Enum
from enum import auto as enum_auto
from typing import Any, Dict, Iterable, List, NamedTuple, Optional, Tuple
from frozenlist import FrozenList

import pandas as pd
import psycopg2
from loguru import logger
from tqdm import tqdm

from platform_management.database_properties import Properties

properties: Properties

warnings.filterwarnings("ignore", category=UserWarning, module="openpyxl")

LOG_HANDLER_ID = logger.add(
    "insert_services.log",
    level="INFO",
    filter=__name__,
    colorize=False,
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: ^8} | {message}",
)
logger = logger.bind(name="insert_services")


class SQLType(Enum):
    INT = enum_auto()
    VARCHAR = enum_auto()
    DOUBLE = enum_auto()
    BOOLEAN = enum_auto()
    SMALLINT = enum_auto()
    JSONB = enum_auto()
    TIMESTAMP = enum_auto()

    @classmethod
    def from_name(cls, name: str):
        if name.lower() in sqltype_mapping:
            return sqltype_mapping[name.lower()]
        if name.startswith("character varying"):
            return SQLType.VARCHAR
        raise ValueError(f"Type {name} cannot be mapped to SQLType")

    @property
    def sql_name(self) -> str:
        return {
            SQLType.INT: "integer",
            SQLType.VARCHAR: "character varying",
            SQLType.DOUBLE: "double precision",
            SQLType.BOOLEAN: "boolean",
            SQLType.SMALLINT: "smallint",
            SQLType.JSONB: "jsonb",
            SQLType.TIMESTAMP: "timestamp with time zone",
        }[self]

    def cast(self, value) -> Any:
        if (
            value is None
            or value != value  # pylint: disable=comparison-with-itself
            or (isinstance(value, str) and value == "")
        ):
            return None
        try:
            if self in (SQLType.INT, SQLType.SMALLINT):
                return int(float(value))
            if self == SQLType.DOUBLE:
                return float(value)
            if self == SQLType.BOOLEAN:
                if isinstance(value, str):
                    return False if value.lower() in ("-", "0", "false", "no", "off", "нет", "ложь") else bool(value)
            if self == SQLType.JSONB:
                return json.dumps(value)
            if self == SQLType.TIMESTAMP:
                if isinstance(value, time.struct_time):
                    return (
                        f"{value.tm_year}-{value.tm_mon:02}-{value.tm_mday:02}"
                        f" {value.tm_hour:02}:{value.tm_min:02}:{value.tm_sec:02}"
                    )
                raise ValueError("Only time.struct_time can be cast to SQL Timestamp")
            raise NotImplementedError(f"Type {self} cast is not implemented for some reason")
        except RuntimeError:
            return None


sqltype_mapping: Dict[str, SQLType] = dict(
    itertools.chain(
        map(
            lambda x: (x, SQLType.VARCHAR),
            ("character varying", "varchar", "str", "string", "text", "varchar", "строка"),
        ),
        map(lambda x: (x, SQLType.DOUBLE), ("double precision", "float", "double", "вещественное", "нецелое")),
        map(lambda x: (x, SQLType.INT), ("integer", "int", "number", "целое")),
        map(lambda x: (x, SQLType.SMALLINT), ("smallint", "малое", "малое целое")),
        map(lambda x: (x, SQLType.JSONB), ("jsonb", "json")),
        map(lambda x: (x, SQLType.BOOLEAN), ("boolean", "булево")),
        map(lambda x: (x, SQLType.TIMESTAMP), ("timestamp", "date", "time", "datetime", "дата", "время")),
    )
)

InsertionMapping = NamedTuple(
    "InsertionMapping",
    (
        ("name", Optional[str]),
        ("opening_hours", Optional[str]),
        ("website", Optional[str]),
        ("phone", Optional[str]),
        ("address", Optional[str]),
        ("capacity", Optional[str]),
        ("osm_id", Optional[str]),
        ("latitude", Optional[str]),
        ("longitude", Optional[str]),
        ("geometry", Optional[str]),
    ),
)


def init_insertion_mapping(
    name: Optional[str] = "Name",
    opening_hours: Optional[str] = "opening_hours",
    website: Optional[str] = "contact:website",
    phone: Optional[str] = "contact:phone",
    address: Optional[str] = "yand_adr",
    osm_id: Optional[str] = "id",
    capacity: Optional[str] = None,
    latitude: str = "x",
    longitude: str = "y",
    geometry: Optional[str] = "geometry",
) -> InsertionMapping:
    fixer = lambda s: None if s in ("", "-") else s
    return InsertionMapping(
        *map(
            fixer,
            (
                name,
                opening_hours,
                website,
                phone,
                address,
                capacity,
                osm_id,
                latitude,
                longitude,
                geometry,
            ),
        )
    )  # type: ignore


def insert_object(
    conn: "psycopg2.connection",
    row: pd.Series,
    phys_id: int,
    name: str,
    service_type_id: int,
    mapping: InsertionMapping,
    properties_mapping: Dict[str, str],
    commit: bool = True,
) -> int:
    """insert_object inserts functional_object.

    service_type_id must be vaild.

    Returns id of the inserted functional object
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT st.capacity_min, st.capacity_max, st.id, cf.id, it.id"
            " FROM city_infrastructure_types it"
            "   JOIN city_functions cf ON cf.city_infrastructure_type_id = it.id"
            "   JOIN city_service_types st ON st.city_function_id = cf.id"
            " WHERE st.id = %s",
            (service_type_id,),
        )
        capacity_min, capacity_max, *ids = cur.fetchone()  # type: ignore
        assert (
            ids[0] is not None and ids[1] is not None and ids[2] is not None
        ), "Service type, city function or infrastructure are not found in the database"
        if mapping.capacity in row and isinstance(row[mapping.capacity], int):
            capacity = row[mapping.capacity]
            is_capacity_real = True
        else:
            capacity = random.randint(capacity_min, capacity_max)
            is_capacity_real = False
        functional_object_properties = {
            db_name: row[row_name]
            for db_name, row_name in properties_mapping.items()
            if row_name in row and row[row_name] is not None and row[row_name] != ""
        }
        cur.execute(
            "INSERT INTO functional_objects (name, opening_hours, website, phone,"
            "       city_service_type_id, city_function_id, city_infrastructure_type_id,"
            "       capacity, is_capacity_real, physical_object_id, properties)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (
                name,
                row.get(mapping.opening_hours),
                row.get(mapping.website),
                row.get(mapping.phone),
                *ids,
                capacity,
                is_capacity_real,
                phys_id,
                json.dumps(functional_object_properties),
            ),
        )
        functional_object_id = cur.fetchone()[0]  # type: ignore
        if commit:
            cur.execute("SAVEPOINT previous_object")
        return functional_object_id


def update_object(
    conn: "psycopg2.connection",
    row: pd.Series,
    functional_object_id: int,
    name: str,
    mapping: InsertionMapping,
    properties_mapping: Dict[str, str],
    commit: bool = True,
) -> int:
    """update_object updates functional_object data.

    Returns id of the updated functional object
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT name, opening_hours, website, phone, capacity, is_capacity_real, properties"
            " FROM functional_objects WHERE id = %s",
            (functional_object_id,),
        )
        res: Tuple[str, str, str, str, int]
        *res, db_properties = cur.fetchone()  # type: ignore
        change = list(
            filter(
                lambda c_v_nw: c_v_nw[1] != c_v_nw[2] and c_v_nw[2] is not None and c_v_nw[2] != "",
                zip(
                    ("name", "opening_hours", "website", "phone", "capacity", "is_capacity_real"),
                    res,
                    (
                        name,
                        row.get(mapping.opening_hours),
                        row.get(mapping.website),
                        row.get(mapping.phone),
                        row.get(mapping.capacity),
                        mapping.capacity in row,
                    ),
                ),
            )
        )
        if res[-1] and not mapping.capacity in row:
            change = change[:-2]
        t = time.localtime()  # pylint: disable=invalid-name
        current_time = f"{t.tm_year}-{t.tm_mon:02}-{t.tm_mday:02} {t.tm_hour:02}:{t.tm_min:02}:{t.tm_sec:02}"
        change.append(("updated_at", "", current_time))
        cur.execute(
            f'UPDATE functional_objects SET {", ".join(list(map(lambda x: x[0] + "=%s", change)))} WHERE id = %s',
            list(map(lambda x: x[2], change)) + [functional_object_id],
        )
        functional_object_properties = {
            db_name: row[row_name]
            for db_name, row_name in properties_mapping.items()
            if row_name in row and row[row_name] is not None and row[row_name] != ""
        }
        if db_properties != functional_object_properties:
            cur.execute(
                "UPDATE functional_objects SET properties = properties || %s::jsonb WHERE id = %s",
                (json.dumps(functional_object_properties), functional_object_id),
            )
        if commit:
            cur.execute("SAVEPOINT previous_object")
        return functional_object_id


def get_properties_keys(conn: "psycopg2.connection", city_service_type: str) -> List[int]:
    """get_properties_keys returns a list of properties keys of a given city_service_type by name or id"""
    with conn.cursor() as cur:
        cur.execute(
            "WITH st AS (SELECT id FROM city_service_types"
            "   WHERE name = %(city_service_type)s or code = %(city_service_type)s)"
            " SELECT DISTINCT jsonb_object_keys(properties)"
            " FROM functional_objects"
            " WHERE city_service_type_id = (SELECT id FROM st)"
            " ORDER BY 1",
            {"city_service_type": city_service_type},
        )
        return [r[0] for r in cur.fetchall()]


def add_objects(
    conn: "psycopg2.connection",
    objects: pd.DataFrame,
    city_name: str,
    service_type: str,
    mapping: InsertionMapping,
    properties_mapping: Dict[str, str] = frozenset({}),
    address_prefixes: List[str] = FrozenList(["Россия, Санкт-Петербург"]),
    new_prefix: str = "",
    commit: bool = True,
    verbose: bool = False,
    log_n: int = 200,
) -> pd.DataFrame:
    """add_objects inserts objects to database.

    Input:

        - `conn` - connection for the database
        - `objects` - DataFrame containing objects
        - `city_name` - name of the city to add objects to. Must be created in the database
        - `service_type` - name of service_type for logging and services names fillment if they are missing
        - `mapping` - InsertionMapping of namings in the database and namings in the DataFrame columns
        - `properties_mapping` - dict[str, str] of additional properties namings in the
            functional_objects.properties and namings in the DataFrame columns
        - `address_prefix` - list of possible prefixes (will be sorted by length)
        - `new_prefix` - unified prefix for all of the inserted objects
        - `commit` - True to commit changes, False for dry run, only resulting DataFrame is returned
        - `verbose` - True to output traceback with errors, False for only error messages printing
        - `log_n` - number of inserted/updated services to log after each

    Return:

        - dataframe of objects with "result" and "functional_obj_id" columns added

    Algorithm steps:

        1. If latitude or longitude is invaild, or address does not start with any of prefixes
            (if `is_service_building` == True), skip

        2. Check if building with given address is present in the database (if `is_service_building` == True)

        3. If found:

            3.1. If functional object with the same name and service_type_id is already
                connected to the physical object, update by calling `update_object`

            3.2. Else get building id and physical_object id

        4. Else:

            4.1. If there is physical object (building if only `is_service_building` == True)
                which geometry contains current object's coordinates,
                get its physical_object id and building id if needed

            4.2. Else insert physical_object/building with geometry type Point

            4.3. Else include inserted ids in the result

        5. Insert functional_object connected to physical_object by calling `insert_object`
    """
    logger.info(
        f'Вставка сервисов запущена из {"командной строки" if __name__ == "__main__" else "внешнего приложения"}'
    )
    logger.info(f'Вставка сервисов типа "{service_type}", всего {objects.shape[0]} объектов')
    logger.info(f'Город вставки - "{city_name}". Список префиксов: {address_prefixes}, новый префикс: "{new_prefix}"')

    if mapping.address in objects.columns:
        objects[mapping.address] = objects[mapping.address].apply(
            lambda x: x.replace("?", "").strip() if isinstance(x, str) else None
        )
    present = 0  # number of inesrting objects already present in the database
    added_to_address, added_to_geom, added_as_points, skipped = 0, 0, 0, 0
    results: List[str] = list(("",) * objects.shape[0])
    functional_ids: List[int] = [-1 for _ in range(objects.shape[0])]
    address_prefixes = sorted(address_prefixes, key=lambda s: -len(s))
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM cities WHERE name = %s", (city_name,))
        city_id = cur.fetchone()
        if city_id is None:
            logger.error(f'Заданный город "{city_name}" отсутствует в базе данных')
            objects["result"] = pd.Series(
                [f'Город "{city_name}" отсутсвует в базе данных'] * objects.shape[0], index=objects.index
            )
            objects["functional_obj_id"] = pd.Series([-1] * objects.shape[0], index=objects.index)
            return objects
        city_id = city_id[0]

        cur.execute(
            "SELECT id, is_building FROM city_service_types WHERE name = %(service)s or code = %(service)s",
            {"service": service_type},
        )
        res = cur.fetchone()
        if res is not None:
            service_type_id, is_service_building = res
        else:
            logger.error(f'Заданный тип сервиса "{service_type}" отсутствует в базе данных')
            objects["result"] = pd.Series(
                [f'Тип сервиса "{service_type}" отсутствует в базе данных'] * objects.shape[0], index=objects.index
            )
            objects["functional_obj_id"] = pd.Series([-1] * objects.shape[0], index=objects.index)
            return objects

        if commit:
            cur.execute("SAVEPOINT previous_object")
        i = 0
        try:
            for i, (_, row) in enumerate(tqdm(objects.iterrows(), total=objects.shape[0])):
                if i % log_n == 0:
                    logger.opt(colors=True).debug(
                        f"Обработано {i:4} сервисов из {objects.shape[0]}:"
                        f" <green>{added_as_points + added_to_address + added_to_geom} добавлены</green>,"
                        f" <yellow>{present} обновлены</yellow>, <red>{skipped} пропущены</red>"
                    )
                    if commit:
                        conn.commit()
                        cur.execute("SAVEPOINT previous_object")
                try:
                    if mapping.geometry in row:
                        try:
                            cur.execute(
                                "WITH tmp AS (SELECT geometry FROM"
                                "       (VALUES (ST_Centroid(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)))"
                                "   ) tmp_inner(geometry))"
                                " SELECT"
                                "   ST_GeometryType((SELECT geometry FROM tmp)) geometry_type,"
                                "   ST_Y((SELECT geometry FROM tmp)) y,"
                                "   ST_X((SELECT geometry FROM tmp)) x",
                                (row[mapping.geometry],),
                            )
                            geom_type, latitude, longitude = cur.fetchone()  # type: ignore
                        except RuntimeError:
                            results[i] = f'Геометрия в поле "{mapping.geometry}" некорректна'
                            if commit:
                                cur.execute("ROLLBACK TO previous_object")
                            else:
                                conn.rollback()
                            continue
                    else:
                        geom_type = "ST_Point"
                        try:
                            latitude = round(float(row[mapping.latitude]), 6)
                            longitude = round(float(row[mapping.longitude]), 6)
                        except RuntimeError:
                            results[i] = "Пропущен (широта или долгота некорректны)"
                            skipped += 1
                            continue
                    address: Optional[str] = None
                    if is_service_building:
                        if mapping.address in row:
                            for address_prefix in address_prefixes:
                                if row.get(mapping.address, "").startswith(address_prefixes):
                                    address = row.get(mapping.address)[len(address_prefix) :].strip(", ")
                                    break
                            else:
                                if len(address_prefixes) == 1:
                                    results[i] = f'Пропущен (Адрес не начинается с "{address_prefixes[0]}")'
                                else:
                                    results[i] = (
                                        "Пропущен (Адрес не начинается ни с одного"
                                        f" из {len(address_prefixes)} префиксов)"
                                    )
                                skipped += 1
                                continue
                    if mapping.geometry not in row and (mapping.latitude not in row or mapping.longitude not in row):
                        results[i] = (
                            "Пропущен (отсутствует как минимум одно необходимое поле:"
                            f" (широта ({mapping.latitude}) + долгота"
                            f" ({mapping.longitude}) или геометрия({mapping.geometry}))"
                        )
                        skipped += 1
                        continue
                    name = row.get(mapping.name, f"({service_type} без названия)")
                    if name is None or name == "":
                        name = f"({service_type} без названия)"

                    cur.execute(
                        "SELECT id FROM municipalities"
                        " WHERE ST_Within(ST_SetSRID(ST_MakePoint(%s, %s), 4326), geometry)",
                        (longitude, latitude),
                    )
                    municipality_id: Optional[int] = cur.fetchone()[0] if cur.rowcount > 0 else None
                    cur.execute(
                        "SELECT id FROM administrative_units"
                        " WHERE ST_Within(ST_SetSRID(ST_MakePoint(%s, %s), 4326), geometry)",
                        (longitude, latitude),
                    )
                    administrative_unit_id: Optional[int] = cur.fetchone()[0] if cur.rowcount > 0 else None

                    phys_id: int
                    build_id: Optional[int]
                    insert_physical_object = False
                    if is_service_building:
                        if address is not None and address != "":
                            cur.execute(
                                "SELECT phys.id, build.id FROM physical_objects phys"
                                "   JOIN buildings build ON build.physical_object_id = phys.id"
                                " WHERE phys.city_id = %s AND build.address LIKE %s AND"
                                "   ST_Distance(phys.center::geography,"
                                "       ST_SetSRID(ST_MakePoint(%s, %s), 4326)::geography) < 100"
                                " LIMIT 1",
                                (city_id, f"%{address}", longitude, latitude),
                            )
                            res = cur.fetchone()  # type: ignore
                        else:
                            res = None
                        if res is not None:
                            # if building with the same address found and distance between point
                            # and the center of geometry is less than 100m
                            phys_id, build_id = res
                            cur.execute(
                                "SELECT id FROM functional_objects f"
                                " WHERE physical_object_id = %s AND city_service_type_id = %s AND name = %s LIMIT 1",
                                (phys_id, service_type_id, name),
                            )
                            res = cur.fetchone()
                            if res is not None:  # if service is already present in this building
                                results[i] = (
                                    f"Обновлен существующий сервис (build_id = {build_id},"
                                    f" phys_id = {phys_id}, functional_object_id = {res[0]})"
                                )
                                functional_ids[i] = res[0]
                                update_object(conn, row, res[0], name, mapping, properties_mapping, commit)
                                present += 1
                                continue
                            added_to_address += 1
                            results[i] = (
                                "Сервис вставлен в здание, найденное по совпадению адреса"
                                f"(build_id = {build_id}, phys_id = {phys_id})."
                            )
                        else:
                            # if no building with the same address found or distance is
                            # too high (address is wrong or it's not a concrete house)
                            if mapping.geometry in row:
                                cur.execute(
                                    "SELECT ST_GeometryType(geometry), phys.id, build.id, build.address"
                                    " FROM physical_objects phys"
                                    "   JOIN buildings build ON build.physical_object_id = phys.id"
                                    " WHERE city_id = %s"
                                    + (" AND municipality_id = %s" if municipality_id is not None else "")
                                    + (" AND administrative_unit_id = %s" if administrative_unit_id is not None else "")
                                    + " AND ST_Intersects(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326), geometry)"
                                    " LIMIT 1",
                                    list(
                                        filter(
                                            lambda x: x is not None,
                                            (city_id, municipality_id, administrative_unit_id, row[mapping.geometry]),
                                        )
                                    ),
                                )
                            else:
                                cur.execute(
                                    "SELECT ST_GeometryType(geometry), phys.id, build.id, build.addres"
                                    " FROM physical_objects phys"
                                    "   JOIN buildings build ON build.physical_object_id = phys.id"
                                    " WHERE city_id = %(city_id)s"
                                    + (
                                        " AND municipality_id = %(municipality_id)s"
                                        if municipality_id is not None
                                        else ""
                                    )
                                    + (
                                        " AND administrative_unit_id = %(administrative_unit_id)s"
                                        if administrative_unit_id is not None
                                        else ""
                                    )
                                    + " AND (ST_GeometryType(geometry) = 'ST_Point'"
                                    "   AND abs(ST_X(geometry) - %(lng)s) < 0.0001"
                                    "   AND abs(ST_Y(geometry) - %(lat)s) < 0.0001"
                                    "   OR ST_Intersects(ST_SetSRID(ST_MakePoint(%(lng)s, %(lat)s), 4326), geometry))"
                                    " LIMIT 1",
                                    {"city_id": city_id, "lng": longitude, "lat": latitude}
                                    | ({"municipality_id": municipality_id} if municipality_id is not None else set())
                                    | (
                                        {"administrative_unit_id": administrative_unit_id}
                                        if administrative_unit_id is not None
                                        else set()
                                    ),
                                )
                            res = cur.fetchone()
                            if res is not None:  # if building found by geometry
                                current_geom_type, phys_id, build_id, address = res
                                cur.execute(
                                    "SELECT id FROM functional_objects f"
                                    " WHERE physical_object_id = %s"
                                    "   AND city_service_type_id = %s AND name = %s LIMIT 1",
                                    (phys_id, service_type_id, name),
                                )
                                res = cur.fetchone()
                                if res is not None:  # if service is already present in this building
                                    if address is not None:
                                        results[i] = (
                                            "Обновлен существующий сервис, находящийся в здании"
                                            f' с другим адресом: "{address}" (build_id = {build_id},'
                                            f" phys_id = {phys_id}, functional_object_id = {res[0]})"
                                        )
                                    else:
                                        results[i] = (
                                            f"Обновлен существующий сервис, находящийся в здании без адреса"
                                            f" (build_id = {build_id}, phys_id = {phys_id},"
                                            f" functional_object_id = {res[0]})"
                                        )
                                    functional_ids[i] = res[0]
                                    update_object(conn, row, res[0], name, mapping, properties_mapping, commit)
                                    present += 1
                                    continue
                                # if no service present, but buiding found
                                added_to_geom += 1
                                if address is None:
                                    results[i] = (
                                        f"Сервис вставлен в здание, подходящее по геометрии, но не имеющее адреса"
                                        f" (build_id = {build_id}, phys_id = {phys_id})"
                                    )
                                else:
                                    results[i] = (
                                        "Сервис вставлен в здание, подходящее по геометрии,"
                                        f' но имеющее другой адрес: "{address}"'
                                        f" (build_id = {build_id}, phys_id = {phys_id})"
                                    )
                                if current_geom_type == "ST_Point" and geom_type != "ST_Point":
                                    cur.execute(
                                        "WITH tmp AS (SELECT geometry FROM"
                                        "       (VALUES (ST_Centroid(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)))"
                                        "   ) tmp_inner(geometry))"
                                        " UPDATE physical_objects"
                                        " SET geometry = (SELECT geometry FORM tmp),"
                                        "   center = ST_Centroid((SELECT geometry FROM tmp))"
                                    )
                                    results[i] += ". Обновлена геометрия здания с точки"
                            else:  # if no building found by address or geometry
                                insert_physical_object = True
                    else:
                        if mapping.geometry in row:
                            cur.execute(
                                "SELECT ST_GeometryType(geometry), id FROM physical_objects phys"
                                " WHERE city_id = %s"
                                "   AND (SELECT EXISTS"
                                "       (SELECT 1 FROM buildings where physical_object_id = phys.id)) = false"
                                + ("  AND municipality_id = %s" if municipality_id is not None else "")
                                + ("  AND administrative_unit_id = %s" if administrative_unit_id is not None else "")
                                + "   AND (ST_CoveredBy(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326), geometry))"
                                " LIMIT 1",
                                list(
                                    filter(
                                        lambda x: x is not None,
                                        (city_id, municipality_id, administrative_unit_id, row[mapping.geometry]),
                                    )
                                ),
                            )
                        else:
                            cur.execute(
                                "SELECT ST_GeometryType(geometry), id FROM physical_objects phys"
                                " WHERE city_id = %(city_id)s"
                                "   AND (SELECT EXISTS"
                                "       (SELECT 1 FROM buildings where physical_object_id = phys.id)) = false"
                                + (" AND municipality_id = %(municipality_id)s" if municipality_id is not None else "")
                                + (
                                    " AND administrative_unit_id = %(administrative_unit_id)s"
                                    if administrative_unit_id is not None
                                    else ""
                                )
                                + "   AND (ST_GeometryType(geometry) = 'ST_Point'"
                                "   AND abs(ST_X(geometry) - %(lng)s) < 0.0001"
                                "   AND abs(ST_Y(geometry) - %(lat)s) < 0.0001"
                                "   OR ST_Intersects(ST_SetSRID(ST_MakePoint(%(lng)s, %(lat)s), 4326), geometry))"
                                " LIMIT 1",
                                {"city_id": city_id, "lng": longitude, "lat": latitude}
                                | ({"municipality_id": municipality_id} if municipality_id is not None else set())
                                | (
                                    {"administrative_unit_id": administrative_unit_id}
                                    if administrative_unit_id is not None
                                    else set()
                                ),
                            )
                        res = cur.fetchone()
                        if res is not None:  # if physical_object found by geometry
                            current_geom_type, phys_id = res
                            cur.execute(
                                "SELECT id FROM functional_objects f "
                                " WHERE physical_object_id = %s AND city_service_type_id = %s AND name = %s"
                                " LIMIT 1",
                                (phys_id, service_type_id, name),
                            )
                            res = cur.fetchone()
                            if res is not None:  # if service is already present in this pysical_object
                                results[i] = (
                                    "Обновлен существующий сервис без здания"
                                    f" (phys_id = {phys_id}, functional_object_id = {res[0]})"
                                )
                                functional_ids[i] = res[0]
                                update_object(conn, row, res[0], name, mapping, properties_mapping, commit)
                                present += 1
                                continue
                            # if no service present, but physical_object found
                            added_to_geom += 1
                            results[i] = (
                                "Сервис вставлен в физический объект," f" подходящий по геометрии (phys_id = {phys_id})"
                            )
                            if current_geom_type == "Point" and geom_type != "Point":
                                cur.execute(
                                    "WITH tmp AS (SELECT geometry FROM (VALUES"
                                    "   (ST_Centroid(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)))) tmp_inner(geometry))"
                                    " UPDATE physical_objects"
                                    " SET geometry = (SELECT geometry FORM tmp),"
                                    "   center = ST_Centroid((SELECT geometry FROM tmp))"
                                )
                                results[i] += ". Обновлена геометрия физического объекта с точки"
                        else:
                            insert_physical_object = True
                    if insert_physical_object:
                        if mapping.geometry in row:
                            cur.execute(
                                "INSERT INTO physical_objects (osm_id, geometry, center, city_id,"
                                "   municipality_id, administrative_unit_id)"
                                " VALUES"
                                " (%s, ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326),"
                                "   ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s, %s, %s)"
                                " RETURNING id",
                                (
                                    row.get(mapping.osm_id),
                                    row[mapping.geometry],
                                    longitude,
                                    latitude,
                                    city_id,
                                    municipality_id,
                                    administrative_unit_id,
                                ),
                            )
                        else:
                            cur.execute(
                                "INSERT INTO physical_objects (osm_id, geometry, center, city_id,"
                                "   municipality_id, administrative_unit_id)"
                                " VALUES"
                                " (%s, ST_SetSRID(ST_MakePoint(%s, %s), 4326),"
                                "   ST_SetSRID(ST_MakePoint(%s, %s), 4326), %s, %s, %s)"
                                " RETURNING id",
                                (
                                    row.get(mapping.osm_id),
                                    longitude,
                                    latitude,
                                    longitude,
                                    latitude,
                                    city_id,
                                    municipality_id,
                                    administrative_unit_id,
                                ),
                            )
                        phys_id = cur.fetchone()[0]  # type: ignore
                        if is_service_building:
                            if address is not None:
                                cur.execute(
                                    "INSERT INTO buildings (physical_object_id, address) VALUES (%s, %s) RETURNING id",
                                    (phys_id, new_prefix + address),
                                )
                                build_id = cur.fetchone()[0]  # type: ignore
                                results[
                                    i
                                ] = f"Сервис вставлен в новое здание (build_id = {build_id}, phys_id = {phys_id})"
                            else:
                                cur.execute(
                                    "INSERT INTO buildings (physical_object_id) VALUES (%s) RETURNING id", (phys_id,)
                                )
                                build_id = cur.fetchone()[0]  # type: ignore
                                results[i] = (
                                    f"Сервис вставлен в новое здание без указания адреса"
                                    f" (build_id = {build_id}, phys_id = {phys_id})"
                                )
                        else:
                            if geom_type != "ST_Point":
                                results[i] = (
                                    "Сервис вставлен в новый физический объект,"
                                    f" добавленный с геометрией (phys_id = {phys_id})"
                                )
                            else:
                                results[i] = (
                                    "Сервис вставлен в новый физический объект, добавленный"
                                    f' с типом геометрии "Точка" (phys_id = {phys_id})'
                                )
                        added_as_points += 1
                    functional_ids[i] = insert_object(
                        conn,
                        row,
                        phys_id,
                        name,
                        service_type_id,
                        mapping,
                        properties_mapping,
                        commit,
                    )  # type: ignore
                except RuntimeError as ex:
                    logger.error("Произошла ошибка: {}", ex, traceback=True)
                    if verbose:
                        logger.error(f"Traceback:\n{traceback.format_exc()}")
                    if commit:
                        cur.execute("ROLLBACK TO previous_object")
                    else:
                        conn.rollback()
                    results[i] = f"Пропущен, вызывает ошибку: {ex}"
                    skipped += 1
        except KeyboardInterrupt:
            logger.warning("Прерывание процесса пользователем")
            logger.opt(colors=True).warning(
                f"Обработано {i+1:4} сервисов из {objects.shape[0]}:"
                f" <green>{added_as_points + added_to_address + added_to_geom} добавлены</green>,"
                f" <yellow>{present} обновлены</yellow>, <red>{skipped} пропущены</red>"
            )
            if commit:
                choice = input("Сохранить внесенные на данный момент изменения? (y/д/1 / n/н/0): ")
                if choice.startswith(("y", "д", "1")):
                    conn.commit()
                    logger.success("Сохранение внесенных изменений")
                else:
                    logger.warning("Отмена внесенных изменений")
                    conn.rollback()
            for j in range(i, objects.shape[0]):
                results[j] = "Пропущен (отмена пользователем)"
        else:
            if commit:
                conn.commit()
    objects["result"] = pd.Series(results, index=objects.index)
    objects["functional_obj_id"] = pd.Series(functional_ids, index=objects.index)
    logger.success(f'Вставка сервисов типа "{service_type}" завершена')
    logger.opt(colors=True).info(
        f"{i+1} сервисов обработано: <green>{added_as_points + added_to_address + added_to_geom} добавлены</green>,"
        f" <yellow>{present} обновлены</yellow>, <red>{skipped} пропущены</red>"
    )
    logger.opt(colors=True).info(
        f"<cyan>{added_as_points} сервисов были добавлены в новые физические объекты/здания</cyan>,"
        f" <green>{added_to_address} добавлены в здания по совпадению адреса</green>,"
        f" <yellow>{added_to_geom} добавлены в физические объекты/здания по совпадению геометрии</yellow>"
    )
    filename = f"insertion_{conn.info.host}_{conn.info.port}_{conn.info.dbname}.xlsx"
    list_name = f'{service_type.replace("/", "_")}_{time.strftime("%Y-%m-%d %H_%M-%S")}'
    logger.opt(colors=True).info(
        "Сохранение лога в файл Excel (нажмите Ctrl+C для отмены, <magenta>но это может повредить файл лога</magenta>)"
    )
    try:
        with pd.ExcelWriter(filename, mode=("a" if os.path.isfile(filename) else "w"), engine="openpyxl") as writer:
            objects.to_excel(writer, list_name)
        logger.info(f'Лог вставки сохранен в файл "{filename}", лист "{list_name}"')
    except RuntimeError as ex:
        newlog = f"Insertion {int(time.time())}.xlsx"
        logger.error(
            f'Ошибка при сохранении лога вставки в файл "{filename}",'
            ' лист "{list_name}": {ex!r}. Попытка сохранения с именем {newlog}'
        )
        try:
            objects.to_excel(newlog, list_name)
            logger.success("Сохранение прошло успешно")
        except RuntimeError as ex:
            logger.error(f"Ошибка сохрангения лога: {ex!r}")
    except KeyboardInterrupt:
        logger.warning(f'Отмена сохранения файла лога, файл "{filename}" может быть поврежден')
    return objects


def replace_with_default(dataframe: pd.DataFrame, default_values: Dict[str, Any]) -> pd.DataFrame:
    """replace_with_default replace null items in dataframe in given columns with given values.

    `default_values` is a dictionary with columns names as key and default values for them as values.

    If column is missing, it will be created filled fully with default values

    Returns new dataframe with null entries replaced with given defaults
    """
    for (column, value) in default_values.items():
        if column in dataframe:
            dataframe[column] = dataframe[column].fillna(value)
        else:
            dataframe[column] = pd.DataFrame([value] * dataframe.shape[0])
    return dataframe


def load_objects_geojson(
    filename: str,
    default_values: Optional[Dict[str, Any]] = None,
    needed_columns: Optional[Iterable[str]] = None,
) -> pd.DataFrame:
    """load_objects_geojson loads objects as DataFrame from geojson. It contains only [features][properties] columns.
    Calls `replace_with_default` after load if `default_values` is present
    """
    with open(filename, "r", encoding="utf-8") as file:
        try:
            data = json.load(file)
            assert "features" in data
        except RuntimeError as exc:
            raise ValueError("Given GeoJSON has wrong format") from exc
        res = pd.DataFrame(
            (entry["properties"] | {"geometry": json.dumps(entry["geometry"])}) for entry in data["features"]
        )
        if default_values is not None:
            res = replace_with_default(res, default_values)
        if needed_columns is not None:
            res = res[needed_columns]
        return res.dropna(how="all").reset_index(drop=True).where(pd.DataFrame.notnull(res), None)


def load_objects_json(
    filename: str, default_values: Optional[Dict[str, Any]] = None, needed_columns: Optional[Iterable[str]] = None
) -> pd.DataFrame:
    """load_objects_json loads objects as DataFrame from json by calling pd.read_json.
    Calls `replace_with_default` after load if `default_values` is present
    """
    res: pd.DataFrame = pd.read_json(filename)
    if default_values is not None:
        res = replace_with_default(res, default_values)
    if needed_columns is not None:
        res = res[needed_columns]
    return res.dropna(how="all").reset_index(drop=True).where(pd.DataFrame.notnull(res), None)


def load_objects_csv(
    filename: str, default_values: Optional[Dict[str, Any]] = None, needed_columns: Optional[Iterable[str]] = None
) -> pd.DataFrame:
    """load_objects_csv loads objects as DataFrame from csv by calling pd.read_csv.

    Calls `replace_with_default` after load if `default_values` is present
    """
    res: pd.DataFrame = pd.read_csv(filename)
    if default_values is not None:
        res = replace_with_default(res, default_values)
    if needed_columns is not None:
        res = res[needed_columns]
    return res.dropna(how="all").reset_index(drop=True).where(pd.DataFrame.notnull(res), None)


def load_objects_xlsx(
    filename: str, default_values: Optional[Dict[str, Any]] = None, needed_columns: Optional[Iterable[str]] = None
) -> pd.DataFrame:
    """load_objects_xlcx loads objects as DataFrame from xlsx by calling pd.read_excel
    (need to install `openpyxl` Pyhton module installed).

    Calls `replace_with_default` after load if `default_values` is present
    """
    res: pd.DataFrame = pd.read_excel(filename, engine="openpyxl")
    if default_values is not None:
        res = replace_with_default(res, default_values)
    if needed_columns is not None:
        res = res[needed_columns]
    return res.dropna(how="all").reset_index(drop=True).where(pd.DataFrame.notnull(res), None)


def load_objects_excel(
    filename: str, default_values: Optional[Dict[str, Any]] = None, needed_columns: Optional[Iterable[str]] = None
) -> pd.DataFrame:
    """load_objects_excel loads objects as DataFrame from xls or ods by calling pd.read_excel
        (need to install `xlrd` Pyhton module installed for xls and `odfpy` for ods).

    Calls `replace_with_default` after load if `default_values` is present
    """
    res: pd.DataFrame = pd.read_excel(filename)
    if default_values is not None:
        res = replace_with_default(res, default_values)
    if needed_columns is not None:
        res = res[needed_columns]
    return res.dropna(how="all").reset_index(drop=True).where(pd.DataFrame.notnull(res), None)


def load_objects(
    filename: str, default_values: Optional[Dict[str, Any]] = None, needed_columns: Optional[Iterable[str]] = None
) -> pd.DataFrame:
    funcs = {
        "csv": load_objects_csv,
        "xlsx": load_objects_xlsx,
        "xls": load_objects_excel,
        "ods": load_objects_excel,
        "json": load_objects_json,
        "geojson": load_objects_geojson,
    }
    try:
        return funcs[filename[filename.rfind(".") + 1 :]](filename, default_values, needed_columns)
    except KeyError as exc:
        raise ValueError(f'File extension "{filename[filename.rfind(".") + 1:]}" is not supported') from exc


def run_cli(
    db_addr: str,
    db_port: int,
    db_name: str,
    db_user: str,
    db_pass: str,
    dry_run: bool,
    verbose: bool,
    log_filename: Optional[str],
    city: str,
    service_type: str,
    document_latitude: str,
    document_longitude: str,
    document_geometry: str,
    document_address: str,
    document_service_name: str,
    document_opening_hours: str,
    document_website: str,
    document_phone: str,
    document_osm_id: str,
    document_capacity: str,
    address_prefix: List[str],
    new_address_prefix: str,
    properties_mapping: List[str],
    filename: str,
):

    global LOG_HANDLER_ID
    if verbose:
        logger.remove(LOG_HANDLER_ID)
        LOG_HANDLER_ID = logger.add(
            "insert_services.log",
            level="DEBUG",
            filter=__name__,
            colorize=False,
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: ^8} | {message}",
        )

    logger.remove(0)
    logger.add(
        sys.stderr,
        level="INFO" if not verbose else "DEBUG",
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: ^8}</level>"
        " | <cyan>{line:03}</cyan> - <level>{message}</level>",
    )

    address_prefixes = list(address_prefix)
    if len(address_prefixes) == 0:
        address_prefixes.append("Россия, Санкт-Петербург")
    else:
        address_prefixes.sort(key=len, reverse=True)

    for entry in properties_mapping:
        if ":" not in entry:
            logger.error(f'Properties mapping "{entry}" does not set a mapping (missing ":"). Exiting')
            sys.exit(1)

    properties_mapping_dict = {entry[: entry.find(":")]: entry[entry.find(":") + 1 :] for entry in properties_mapping}

    if not os.path.isfile(filename):
        logger.error(f'Входной файл "{filename}" не найден или не является файлом, завершение работы')
        sys.exit(1)

    if log_filename is None:
        t = time.localtime()  # pylint: disable=invalid-name
        fname: str
        if os.path.sep in os.path.relpath(filename):  # type: ignore
            fname = os.path.relpath(filename)[os.path.relpath(filename).rfind(os.path.sep) + 1 :]  # type: ignore
        else:
            fname = filename

        logfile = (
            f"{t.tm_year}-{t.tm_mon:02}-{t.tm_mday:02} "
            f"{t.tm_hour:02}-{t.tm_min:02}-{t.tm_sec:02}-"
            f'{fname[:fname.rfind(".")] if "." in fname else fname}.csv'
        )
        if os.path.isfile(logfile):
            logfile = f"{logfile[-4:]}-{time.time()}.csv"
        del fname
    else:
        if log_filename not in ("", "-"):
            logfile = log_filename
        else:
            logfile = None

    mapping = init_insertion_mapping(
        document_service_name,
        document_opening_hours,
        document_website,
        document_phone,
        document_address,
        document_osm_id,
        document_capacity,
        document_latitude,
        document_longitude,
        document_geometry,
    )
    logger.info(f"Соответствие (маппинг) документа: {mapping}")

    logger.opt(colors=True).info(f"Подключение к базе данных <cyan>{db_user}@{db_addr}:{db_port}/{db_name}</cyan>.")

    conn = psycopg2.connect(
        host=db_addr, port=db_port, dbname=db_name, user=db_user, password=db_pass, connect_timeout=20
    )

    if dry_run:
        logger.warning("Холостой запуск, изменения не будут сохранены")
    else:
        logger.info("Загруженные объекты будут сохранены в базе данных")
    if logfile is not None:
        logger.opt(colors=True).info(f'Сохарнение лога будет произведено в файл <cyan>"{logfile}"</cyan>')

    objects: pd.DataFrame = load_objects(filename)
    logger.info(f'Загружено {objects.shape[0]} объектов из файла "{filename}"')
    for column in mapping._fields:
        value = getattr(mapping, column)
        if value is not None and value not in objects.columns:
            logger.warning(f'Колонка "{value}" используется ({column}), но не задана в файле')

    objects = add_objects(
        conn,
        objects,
        city,
        service_type,
        mapping,
        properties_mapping_dict,
        address_prefixes,
        new_address_prefix,
        not dry_run,
        verbose,
    )

    if logfile is not None:
        objects.to_csv(logfile)
    logger.opt(colors=True).info(f'Завершено, лог записан в файл <green>"{logfile}"</green>')
