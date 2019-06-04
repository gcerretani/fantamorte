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
import datetime
import dateutil.parser

people = {}

def get_data(myname):
    if myname in people:
        return people[myname]
    else: 
        pdata = {}
        p = wptools.page(myname, lang='it', silent=True)
        p.wanted_labels([])
        page = p.get_wikidata()
#         for item in page.data:
#             print(item , page.data[item])

        name = page.data['label']
        descr = page.data['description']
        claims = page.data['claims']
        birth = None
        death = None
        nobel = False
        alive = False

        if 'P569' in claims:
            d = claims['P569'][0][1:]
            birth = dateutil.parser.parse(d)
        else:
            birth = None
            print("no birth date")

        if 'P570' in claims:
            d = claims['P570'][0][1:]
            death = dateutil.parser.parse(d)
        else:
            alive = True
            death = None

        if 'P3188' in claims:
            nobel = True
        else:
            nobel = False

#         if 'P166' in claims:
#             for i in claims['P166']:
#                 print(i)

        age = calculate_age(birth,death)

        pdata['name'] = name
        pdata['descr'] = descr
        pdata['birth'] = birth
        pdata['death'] = death
        pdata['age'] = age
        pdata['alive'] = alive
        pdata['nobel'] = nobel

        people[myname] = pdata

        return pdata

def calculate_age(birth, death = None):
    if death:
        today = death
    else:
        today = datetime.date.today()
    return today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))


with open('names.txt') as names:
    for myname in names:
        myname = myname.strip('\n')

        data = get_data(myname)

        if not data['alive']:
            print(data['name'], data['age'], data ['alive'], data['nobel'])

