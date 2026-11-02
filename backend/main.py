import re

import copy
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from icalendar import Calendar, Event
from fastapi import FastAPI, UploadFile, File
from fastapi.responses import StreamingResponse
import io
import csv
import zipfile
app = FastAPI()

TIMEZONE = ZoneInfo("Europe/Amsterdam")

def new_calendar():
    calendar = Calendar()
    calendar.add('prodid', '-//Room Responsible//room-responsible-ng//EN')
    calendar.add('version', '2.0')
    return calendar

@app.get("/")
async def root():
    return {"message": "Hello World"}

@app.post("/process")
async def process_csv(file: UploadFile = File(...)):
    contents = await file.read()
    text = contents.decode("utf-8")
    
    csv_file = io.StringIO(text)
    reader = csv.reader(csv_file, delimiter=",")

    PERSONS, DATES, SHIFTS, BIN_WEIGHTS = read_availabilities(reader)
    NO_ONE = Person("Get Room Responsible", SHIFTS)
    solvescip(PERSONS, DATES, SHIFTS, BIN_WEIGHTS)
    for i in PERSONS:
        i.assign_from_bin(DATES, SHIFTS)

    zip_bytes = print_results(PERSONS, DATES, SHIFTS, BIN_WEIGHTS, NO_ONE)

    return StreamingResponse(
        io.BytesIO(zip_bytes),
        media_type="application/zip",
        headers={
            "Content-Disposition": "attachment; filename=results.zip"
        }
    )


class Person:
    def __init__(self, name, SHIFTS):
        self.name = name
        self.is_board = False
        self.max_shifts = -1
        self.bin_preference = []
        self.bin_assign = []
        self.shift_assigned = {}
        for i in SHIFTS:
            self.shift_assigned[i.get_indicator()] = 0
        self.assigned = 0
        self.available = 0
        self.calendar = new_calendar()

    def set_board(self, val):
        self.is_board = val

    def set_max_shifts(self, number):
        self.max_shifts = number

    def __str__(self):
        return f'Person({self.name}, {self.is_board}, {self.max_shifts})'

    def add_indicated_shift(self, indicator):
        self.shift_assigned[indicator] += 1

    def get_name(self):
        return self.name

    def get_available(self):
        return self.available

    def get_total(self):
        return self.assigned

    def get_total_hours(self, BIN_WEIGHTS):
        return sum([self.bin_assign[i] * BIN_WEIGHTS[i] for i in range(len(self.bin_assign))])

    def set_bin_assignment(self, assignment_list):
        self.bin_assign = assignment_list

    def get_bin_preference(self):
        return self.bin_preference

    def get_bin_assignment(self):
        return self.bin_assign

    def get_is_board(self):
        return self.is_board

    def get_max_shifts(self):
        return self.max_shifts

    def get_indicated_shift(self, indicator):
        return self.shift_assigned[indicator]

    def assign_from_bin(self, DATES, SHIFTS):
        for i in range(len(DATES)):
            for j in range(len(SHIFTS)):
                if self.bin_assign[i * len(SHIFTS) + j] == 1:
                    shift = DATES[i].get_shifts()[j]
                    shift.assign_person(self)
                    self.add_indicated_shift(SHIFTS[j].get_indicator())
                    self.assigned += 1
        self.available = sum(self.bin_assign)

    def get_calendar(self):
        return self.calendar

class Date:
    def __init__(self, exams, date):
        self.exams = exams
        self.shifts = []
        self.date = date

    def add_shift(self, shift):
        self.shifts.append(shift)

    def is_exams(self):
        return self.exams

    def get_shifts(self):
        return self.shifts

    def get_date(self):
        return self.date

    def __str__(self):
        string = f'Date({self.date}), consisting of shifts: \n'
        for i in self.shifts:
            string += f'- {str(i)}\n'
        return string

class Shift:
    def __init__(self, start, end, indicator):
        self.start = datetime.strptime(start, "%H:%M:%S")
        self.end = datetime.strptime(end, "%H:%M:%S")
        self.indicator = indicator
        self.assigned_people = []

    def __str__(self):
        string = f'Shift ({self.indicator}, {datetime.strftime(self.start, "%H:%M:%S")} - {datetime.strftime(self.end, "%H:%M:%S")}), filled by: '
        for i in self.assigned_people:
            string += f'{i.get_name()}, '
        return string

    def set_end_time(self, end):
        self.end = datetime.strptime(end, "%H:%M:%S")

    def assign_person(self, person):
        self.assigned_people.append(person)

    def get_indicator(self):
        return self.indicator

    def get_assigned_persons(self):
        return self.assigned_people

    def get_start_time(self):
        return self.start

    def get_end_time(self):
        return self.end

    def get_hours(self):
        return (self.end - self.start).total_seconds() / 3600

from pyscipopt import Model, quicksum 
def solvescip(PERSONS, DATES, SHIFTS, BIN_WEIGHTS):
    SHIFTSTOT = len(SHIFTS) * len(DATES)

    N = [] # People with max shifts assigned
    B = [] # Board members

    m = Model()

    for j, person in enumerate(PERSONS):
        if person.get_is_board():
            B.append(j)
        if person.get_max_shifts() != -1:
            N.append(j)


    # Variables
    r = [m.addVar(vtype="I", name=f"r_{i}") for i in range(SHIFTSTOT)]
    b = [m.addVar(vtype="I", name=f"b_{i}") for i in range(SHIFTSTOT)]
    x = [[m.addVar(vtype="B", name=f"x_{i}_{j}") for j in range(len(PERSONS))] for i in range(SHIFTSTOT)]
    n = [m.addVar(vtype="I", name=f"n_{i}") for i in range(len(N))]
    bv = [m.addVar(vtype="C", name=f"bv_{i}") for i in range(len(B))]

    # Availability constraint
    for i in range(SHIFTSTOT):
        for j, person in enumerate(PERSONS):
            m.addCons(x[i][j] <= person.get_bin_preference()[i], f"available_{i}_{j}")


    for i in range(SHIFTSTOT):
        # Extra variable for people assigned to shift
        m.addCons(quicksum(x[i]) == r[i], f"rge_{i}")
        m.addCons(r[i] <= 2, f"ass_1_{i}")

        # All shifts have at least one board member
        m.addCons(quicksum(x[i][j] for j in B) >= b[i], f"boardav_{i}")

    # Reward a person doing lunch (L) on a day they also do the morning (M) or
    # afternoon (A); lunch[d][j] can only be 1 if both hold.
    lunch = [[m.addVar(vtype="B", name=f"lunch_{d}_{j}") for j in range(len(PERSONS))] for d in range(len(DATES))]
    for d in range(len(DATES)):
        morning, midday, afternoon = x[3 * d], x[3 * d + 1], x[3 * d + 2]
        for j in range(len(PERSONS)):
            m.addCons(lunch[d][j] <= midday[j], f"lunch_l_{d}_{j}")
            m.addCons(lunch[d][j] <= morning[j] + afternoon[j], f"lunch_ma_{d}_{j}")

    # People with a max shifts get at most that many shifts, of any type.
    for i, j in enumerate(N):
        person = PERSONS[j]
        m.addCons(quicksum(get_column(x, j)) == n[i], f"n_{j}")
        m.addCons(n[i] <= person.get_max_shifts(), f"maxshift_{j}")

    for i, j in enumerate(B):
        m.addCons(wegrsum(get_column(x, j), BIN_WEIGHTS) == bv[i], f"bv_{j}")

    # Board members' hours should be balanced: penalize the gap between the
    # busiest and the least busy board member.
    board_max = m.addVar(vtype="C", name="board_max")
    board_min = m.addVar(vtype="C", name="board_min")
    for k in range(len(B)):
        m.addCons(bv[k] <= board_max, f"board_max_{k}")
        m.addCons(bv[k] >= board_min, f"board_min_{k}")
    # One extra shift widens the gap by at most 4 here plus 1 from
    # spread_penalty, still below the 6 a board member earns for filling a
    # slot (5 + 1), so balancing never leaves slots empty.
    board_penalty = 4 * (board_max - board_min) / max(BIN_WEIGHTS)

    # Each person's busiest week, in hours. Penalizing it makes the solver
    # prefer spreading someone's shifts over the weeks instead of stacking them.
    weeks = {}
    for d, date in enumerate(DATES):
        weeks.setdefault(date.get_date().isocalendar()[:2], []).append(d)
    peak = [m.addVar(vtype="C", name=f"peak_{j}") for j in range(len(PERSONS))]
    for j in range(len(PERSONS)):
        for w, days in enumerate(weeks.values()):
            week_hours = quicksum(BIN_WEIGHTS[i] * x[i][j] for d in days for i in range(d * len(SHIFTS), (d + 1) * len(SHIFTS)))
            m.addCons(week_hours <= peak[j], f"peak_{j}_{w}")
    # Scaled so one extra shift costs at most 1, less than the 5 for filling it
    spread_penalty = quicksum(peak) / max(BIN_WEIGHTS)

    m.setObjective(5 * quicksum(r) + quicksum(b) + quicksum(n) + quicksum(lunch[d][j] for d in range(len(DATES)) for j in range(len(PERSONS))) - board_penalty - spread_penalty, sense="maximize")

    m.setParam("limits/time", 30)
    m.optimize()

    bin_prefs = [[0 for _ in range(SHIFTSTOT)] for _ in range(len(PERSONS))]
    for v in m.getVars():
        index = re.split(r'_', str(v))
        if (index[0] == "x"):
            shift, person = int(index[1]), int(index[2])
            bin_prefs[person][shift] = round(m.getVal(v))

    for i in range(len(PERSONS)):
        PERSONS[i].set_bin_assignment(bin_prefs[i])

def wegrsum(x, weights):
    obj = 0
    for i, expr in enumerate(x):
        obj += weights[i] * expr
    return obj

def get_column(x, i) -> list:
    return [row[i] for row in x]

def print_results(PERSONS, DATES, SHIFTS, BIN_WEIGHTS, NO_ONE):
    zip_buffer = io.BytesIO()

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zipf:

        # -----------------------------
        # OpenhoudenResults.csv
        # -----------------------------
        results_csv = io.StringIO()
        results_csv.write('Subject,Start Date,Start Time,End Date,End Time\n')

        for date in DATES:
            for shift in date.get_shifts():
                while len(shift.get_assigned_persons()) < 2:
                    shift.assign_person(copy.deepcopy(NO_ONE))

                p1, p2 = shift.get_assigned_persons()[:2]
                line = (
                    f'{p1.get_name()} & {p2.get_name()},'
                    f'{datetime.strftime(date.get_date(), "%d/%m/%Y")},'
                    f'{datetime.strftime(shift.get_start_time(), "%H:%M:%S")},'
                    f'{datetime.strftime(date.get_date(), "%d/%m/%Y")},'
                    f'{datetime.strftime(shift.get_end_time(), "%H:%M:%S")}\n'
                )
                results_csv.write(line)

        zipf.writestr(
            "OpenhoudenResults.csv",
            results_csv.getvalue().encode("utf-8-sig")
        )

        # -----------------------------
        # OpenhouderStats.csv
        # -----------------------------
        stats_csv = io.StringIO()
        stats_csv.write("STATS\n")

        headers = 'Shift\\Person,' + ','.join(p.get_name() for p in PERSONS) + '\n'
        stats_csv.write(headers)

        stats_csv.write('Available,' + ','.join(str(p.get_available()) for p in PERSONS) + '\n')
        stats_csv.write('Total,' + ','.join(str(p.get_total()) for p in PERSONS) + '\n')
        stats_csv.write('Totalhours,' + ','.join(f'{p.get_total_hours(BIN_WEIGHTS):g}' for p in PERSONS) + '\n')

        for shift in SHIFTS:
            row = [shift.get_indicator()]
            for person in PERSONS:
                row.append(str(person.get_indicated_shift(shift.get_indicator())))
            stats_csv.write(','.join(row) + '\n')

        zipf.writestr(
            "OpenhouderStats.csv",
            stats_csv.getvalue().encode("utf-8-sig")
        )

        # -----------------------------
        # Calendar files
        # -----------------------------
        cal = new_calendar()

        for date in DATES:
            for shift in date.get_shifts():
                assigned_persons = shift.get_assigned_persons()

                event = Event()
                # Stable per date and shift, so re-importing a new schedule
                # updates events instead of duplicating them
                event.add('uid', f'{date.get_date():%Y%m%d}-{shift.get_indicator()}@room-responsible')
                event.add('summary', ' & '.join(p.get_name() for p in assigned_persons))
                event.add('dtstart', datetime.combine(date.get_date(), shift.get_start_time().time(), TIMEZONE))
                event.add('dtend', datetime.combine(date.get_date(), shift.get_end_time().time(), TIMEZONE))
                event.add('dtstamp', datetime.now(timezone.utc))
                event.add('location', 'MF 3.155')
                event.add('description', 'Room Responsible Shift')

                cal.add_component(event)
                for person in assigned_persons:
                    person.get_calendar().add_component(event)

        # VTIMEZONE definitions only need to cover the scheduled dates
        first_date = min(d.get_date() for d in DATES).date()
        last_date = max(d.get_date() for d in DATES).date() + timedelta(days=1)
        cal.add_missing_timezones(first_date, last_date)
        zipf.writestr("OpenhoudenSchedule.ics", cal.to_ical())

        for person in PERSONS:
            person.get_calendar().add_missing_timezones(first_date, last_date)
            zipf.writestr(
                f"schedules/Openhouden{person.get_name()}.ics",
                person.get_calendar().to_ical()
            )

    zip_buffer.seek(0)
    return zip_buffer.getvalue()

def read_availabilities(reader):
    PERSONS = []
    DATES = []
    SHIFTS = []
    BIN_WEIGHTS = []

    # The amount of cells one Shift takes in the csv file (start, end, indicator)
    SHIFTCSV = 3
    # The amount of shifts in a day
    SHIFTSPERDAY = 3
    # How many datacolumns each date has (date + is_exam currently) 
    DATEDATA = 2
    # How man rows of information before the dates start 
    DATEDATASTART = 4
    # The time the A shifts ends on exam days
    EXAMENDTIME = "18:00:00"

    # Read all lines
    for index, line in enumerate(reader):
        # Read first line, which are the shifts
        if index == 0:
            shifts = line
            for i in range(SHIFTSPERDAY):
                SHIFTS.append(Shift(*[shifts[i * SHIFTCSV + j] for j in range(SHIFTCSV)]))
        elif index == 1:
            # Spreadsheet exports pad every row to the widest row (the shift
            # header), so trailing empty columns are not people.
            persons = line[DATEDATA:]
            while persons and not persons[-1].strip():
                persons.pop()
            for i in range(len(persons)):
                PERSONS.append(Person(persons[i], SHIFTS))
        elif index == 2:
            max_shifts = line[DATEDATA:DATEDATA + len(PERSONS)]
            for i in range(len(max_shifts)):
                PERSONS[i].set_max_shifts(int(max_shifts[i]))
        elif index == 3:
            board = line[DATEDATA:DATEDATA + len(PERSONS)]
            for i in range(len(board)):
                PERSONS[i].set_board(int(board[i]))
        else:
            data = line
            dt = datetime.strptime(data[0], "%d/%m/%Y")
            DATES.append(Date(exams = int(data[1]), date = dt))

            availabilities = line[DATEDATA:DATEDATA + len(PERSONS)]
            for i in SHIFTS:
                shift = copy.deepcopy(i)
                if DATES[index - DATEDATASTART].is_exams() and shift.get_indicator() == "A":
                    shift.set_end_time(EXAMENDTIME)
                DATES[index - DATEDATASTART].add_shift(shift)
                BIN_WEIGHTS.append(shift.get_hours())
            for i, v in enumerate(availabilities):
                for j in DATES[index - DATEDATASTART].get_shifts():
                    if j.get_indicator() in v:
                        PERSONS[i].bin_preference.append(1)
                    else:
                        PERSONS[i].bin_preference.append(0)
    return PERSONS, DATES, SHIFTS, BIN_WEIGHTS
