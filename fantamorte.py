"""
Copyright (C) 2019  Giovanni Cerretani

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""

import wptools
import sys
from datetime import datetime, timezone
from dateutil.parser import parse
from dateutil.relativedelta import relativedelta


class Person:
    def __init__(self, name):
        wp_parser = wptools.page(name, lang="it", silent=True)
        wp_parser.wanted_labels([])
        self._page = wp_parser.get_wikidata().data
        self._name = self._page["label"]
        self._descr = self._page["description"]
        self._claims = self._page["claims"]
        self._birth_date = self.get_date("P569")
        self._death_date = self.get_date("P570")
        self._is_alive = not self.has_property("P570")
        self._age = self.calculate_age()
        self._nobel = self.has_property("P3188")
        self._oscar = self.has_oscar()

    def get_date(self, id):
        if id not in self._claims:
            return None
        d = self._claims[id][0][1:]
        try:
            return parse(d)  # may be invalid
        except:
            return None

    def has_property(self, it):
        return it in self._claims

    def has_oscar(self):
        if self.has_property("P166"):
            for award_id in self._claims["P166"]:
                try:
                    award_parser = wptools.page(wikibase=award_id, silent=True).get()
                    award_parser.wanted_labels([])
                    award_page_claims = award_parser.get_wikidata().data["claims"]
                    if "P31" in award_page_claims:
                        if "Q19020" in award_page_claims["P31"]:
                            return True
                except:
                    continue
        return False

    def calculate_age(self):
        if not self._is_alive:
            last_day = self._death_date
        else:
            last_day = datetime.now(timezone.utc)
        return relativedelta(last_day, self._birth_date).years

    @property
    def name(self):
        return self._name

    @property
    def is_alive(self):
        return self._is_alive

    @property
    def birth_date(self):
        return self._birth_date

    @property
    def death_date(self):
        return self._death_date

    @property
    def age(self):
        return self._age

    @property
    def nobel(self):
        return self._nobel

    @property
    def oscar(self):
        return self._oscar


with open(sys.argv[1]) as names:
    for name in names:
        name = name.strip("\n")

        p = Person(name)

        print(p.name, p.age, p.birth_date, p.death_date, p.nobel, p.oscar)
