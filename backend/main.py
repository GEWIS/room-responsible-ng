import re

import copy
from datetime import datetime
from icalendar import Calendar, Event
from fastapi import FastAPI, UploadFile, File
from fastapi.responses import StreamingResponse
import io
import csv
import zipfile
app = FastAPI()

@app.get("/")
async def root():
    return {"message": "Hello World"}

@app.post("/process")
async def process_csv(file: UploadFile = File(...)):
    # process CSV -> create zip
    contents = await file.read()
    text = contents.decode("utf-8")
    
    csv_file = io.StringIO(text)
    reader = csv.reader(csv_file, delimiter=",")

    PERSONS, DATES, SHIFTS, BIN_WEIGHTS = read_availabilities(reader)
    NO_ONE = Person("Get Room Responsible", SHIFTS)
    solvescip(PERSONS, DATES, SHIFTS, BIN_WEIGHTS)
    for i in PERSONS:
        i.assign_from_bin(DATES, SHIFTS)

    zip_bytes = print_results(PERSONS, DATES, SHIFTS, NO_ONE)

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
        self.calendar = []
        self.non_busy = 0
        self.busy = 0
        self.is_board = False
        self.max_shifts = -1
        self.bin_preference = []
        self.bin_assign = []
        self.shift_assigned = {}
        for i in SHIFTS:
            self.shift_assigned[i.get_indicator()] = 0
        self.assigned = 0
        self.available = 0
        self.calendar = Calendar()

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

    def set_bin_preference(self, preferred_list):
        self.bin_preference = preferred_list
        self.available = sum(preferred_list)

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

    def increment_assigned(self):
        self.assigned += 1

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
    def __init__(self, exams, is_monday, date, DATES):
        self.exams = exams
        self.is_monday = is_monday
        self.shifts = []
        self.date = date
        if len(DATES) > 0:
            self._last_date = DATES[-1]
        else:
            self._last_date = None
        if len(DATES) > 1:
            self._second_last_date = DATES[-2]
        else:
            self._second_last_date = None
        if len(DATES) > 2:
            self._third_last_date = DATES[-3]
        else:
            self._third_last_date = None

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
    def __init__(self, start, end, indicator, weight):
        self.start = datetime.strptime(start, "%H:%M:%S")
        self.end = datetime.strptime(end, "%H:%M:%S")
        self.indicator = indicator
        self.available_people = []
        self.assigned_people = []
        self.weight = int(weight)

    def __str__(self):
        string = f'Shift ({self.indicator}, {datetime.strftime(self.start, "%H:%M:%S")} - {datetime.strftime(self.end, "%H:%M:%S")}), filled by: '
        for i in self.assigned_people:
            string += f'{i.get_name()}, '
        return string

    def set_weight(self, val):
        self.weight = val

    def add_available_person(self, person):
        self.available_people.append(person)

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

    def get_weight(self):
        return self.weight

from pyscipopt import Model, quicksum 
from pyscipopt.recipes import nonlinear
def solvescip(PERSONS, DATES, SHIFTS, BIN_WEIGHTS):
    SHIFTSTOT = len(SHIFTS) * len(DATES)

    N = [] # People with max shifts assigned
    B = [] # Board members
    BI = [] # Board members with infinite shifts

    m = Model()

    for j, person in enumerate(PERSONS):
        if person.get_is_board():
            B.append(j)
            if person.get_max_shifts() == -1:
                BI.append(j)
        if person.get_max_shifts() != -1:
            N.append(j)


    # Variables
    r = [m.addVar(vtype="I", name=f"r_{i}") for i in range(SHIFTSTOT)]
    b = [m.addVar(vtype="I", name=f"b_{i}") for i in range(SHIFTSTOT)]
    x = [[m.addVar(vtype="B", name=f"x_{i}_{j}") for j in range(len(PERSONS))] for i in range(SHIFTSTOT)]
    # x = m.addMVar(shape=(SHIFTSTOT, len(PERSONS)), vtype="B", name="x")
    l = [m.addVar(vtype="I", name=f"l_{i}") for i in range(SHIFTSTOT//3)]
    n = [m.addVar(vtype="I", name=f"n_{i}") for i in range(len(N))]
    bv = [m.addVar(vtype="I", name=f"bv_{i}") for i in range(len(B))]
    mean = m.addVar(lb=None, name="mean")

    # Availability constraint
    for i in range(SHIFTSTOT):
        for j, person in enumerate(PERSONS):
            m.addCons(x[i][j] <= person.get_bin_preference()[i], f"available_{i}_{j}")


    for i in range(SHIFTSTOT):
        # Extra variable for people assigned to shift
        m.addCons(quicksum(x[i]) == r[i], f"rge_{i}")
        m.addCons(r[i] <= 2, f"ass_1_{i}")

        # All shifts have at least one board member
        sum = quicksum([x[i][j] for j in B])
        m.addCons(sum >= b[i], f"boardav_{i}")

    for i in range(0, SHIFTSTOT, 3):
        l1 = rowmult(x[i], x[i+1])
        l2 = rowmult(x[i+2], x[i+1])
        l3 = rowmult(l1, l2)
        l4 = quicksum(l1) + quicksum(l2) - quicksum(l3)
        m.addCons(l[i // 3] == l4, f"l_{i//3}")
        # m.addConstr(l[i // 3] <= 1, f"ltop_{i//3}")
         
    # People with a max shifts get maximum their max shifts. 
    for i, j in enumerate(N):
        person = PERSONS[j]
        m.addCons(wegrsum(get_column(x, j), BIN_WEIGHTS) == n[i], f"n_{j}")
        m.addCons(n[i] <= person.get_max_shifts(), f"maxshift_{j}")

    for i, j in enumerate(B):
        m.addCons(wegrsum(get_column(x, j), BIN_WEIGHTS) == bv[i], f"bv_{j}")

    # Constraint for mean
    m.addCons(mean == (1/len(B)) * quicksum(bv[i] for i in B))

    # Variance expression
    variance = (1/len(B)) * quicksum((bv[i] - mean)*(bv[i] - mean) for i in B)
    # Objective: minimize variance

    # m.setObjective(5 * quicksum(r) + quicksum(b) + quicksum(n) + quicksum(l) - variance, sense='maximize')
    nonlinear.set_nonlinear_objective(m, 5 * quicksum(r) + quicksum(b) + quicksum(n) + quicksum(l) - variance, sense='maximize')

    # Set maximization objectives
    # m.setObjectiveN(grsum(r), 0, 0)
    # m.setObjectiveN(grsum(b), 1, 1)
    # m.setObjectiveN(grsum(n), 2, 2)
    # m.setObjectiveN(-variance, 3, 3)
    # m.setObjectiveN(-grsum(var), 2, 2)

    m.optimize()

    for v in m.getVars():
        try: 
            print('%s %g' % (v, m.getVal(v)))
        except:
            print('%s' % (v))

    bin_prefs = [[0 for _ in range(SHIFTSTOT)] for _ in range(len(PERSONS))]
    for v in m.getVars():
        index = re.split(r'_', str(v))
        if (index[0] == "x"):
            shift, person = int(index[1]), int(index[2])
            bin_prefs[person][shift] = int(m.getVal(v))

    for i in range(len(PERSONS)):
        PERSONS[i].set_bin_assignment(bin_prefs[i])
        print(PERSONS[i].get_bin_assignment())
        print(len([x for i, x in enumerate(PERSONS[i].get_bin_assignment()) if i % 3 == 1]))

def rowmult(x1, x2):
    # obj = gp.LinExpr()
    obj = []
    for i, j in zip(x1, x2):
        obj.append(i * j)
    return obj

def wegrsum(x, weights):
    obj = 0
    for i, expr in enumerate(x):
        obj += weights[i] * expr
    return obj

def get_column(x, i) -> list:
    return [row[i] for row in x]


def get_person_by_name(name, PERSONS):
    for i in PERSONS:
        if i.get_name() == name:
            return i

def print_results(PERSONS, DATES, SHIFTS, NO_ONE):
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
        cal = Calendar()

        for date in DATES:
            for shift in date.get_shifts():
                assigned_persons = shift.get_assigned_persons()

                event = Event()
                event.add('summary', ' & '.join(p.get_name() for p in assigned_persons))
                event.add('dtstart', datetime.combine(date.get_date(), shift.get_start_time().time()))
                event.add('dtend', datetime.combine(date.get_date(), shift.get_end_time().time()))
                event.add('dtstamp', datetime.now())
                event.add('location', 'MF 3.155')
                event.add('description', 'Room Responsible Shift')

                cal.add_component(event)
                for person in assigned_persons:
                    person.get_calendar().add_component(event)

        zipf.writestr("OpenhoudenSchedule.ics", cal.to_ical())

        for person in PERSONS:
            zipf.writestr(
                f"schedules/Openhouden{person.get_name()}.ics",
                person.get_calendar().to_ical()
            )

    zip_buffer.seek(0)
    return zip_buffer.getvalue()

# def print_results(PERSONS, DATES, SHIFTS, NO_ONE):
#     # Write resulting shifts to file with UTF-8 encoding
#     with open('OpenhoudenResults.csv', 'w', encoding='utf-8-sig') as file:
#         file.write(f'Subject, Start Date, Start Time, End Date, End Time \n')
#         for date in DATES:
#             for shift in date.get_shifts():
#                 room_responsible_shift = ""
#                 while len(shift.get_assigned_persons()) < 2:
#                     shift.assign_person(copy.deepcopy(NO_ONE))
#                 room_responsible_shift += f'{shift.get_assigned_persons()[0].get_name()} & {shift.get_assigned_persons()[1].get_name()},'
#                 room_responsible_shift += f'{datetime.strftime(date.get_date(), "%d/%m/%Y")}, {datetime.strftime(shift.get_start_time(), "%H:%M:%S")}, {datetime.strftime(date.get_date(), "%d/%m/%Y")}, {datetime.strftime(shift.get_end_time(), "%H:%M:%S")} \n'
#                 file.write(room_responsible_shift)
#
#     with open("OpenhouderStats.csv", "w", encoding='utf-8-sig') as file:  # Use UTF-8 encoding
#         file.write("STATS\n")
#
#         # Create headers for each person
#         headers = f'Shift\\Person,' + ','.join([person.get_name() for person in PERSONS]) + '\n'
#         file.write(headers)
#
#         # Write availability and total assignments
#         file.write('Available,' + ','.join(str(person.get_available()) for person in PERSONS) + '\n')
#         file.write('Total,' + ','.join(str(person.get_total()) for person in PERSONS) + '\n')
#
#         # Write shift assignment information
#         for shift in SHIFTS:
#             shift_row = [shift.get_indicator()]
#             for person in PERSONS:
#                 shift_row.append(str(person.get_indicated_shift(shift.get_indicator())))
#             file.write(','.join(shift_row) + '\n')
#
#     cal = Calendar()
#
#     for date in DATES:
#         for shift in date.get_shifts():
#             assigned_persons = shift.get_assigned_persons()
#             event = Event()
#             event.add('summary', ' & '.join([person.get_name() for person in assigned_persons]))
#             event.add('dtstart', datetime.combine(date.get_date(), shift.get_start_time().time()))
#             event.add('dtend', datetime.combine(date.get_date(), shift.get_end_time().time()))
#             event.add('dtstamp', datetime.now())
#             event.add('location', 'MF 3.155')
#             event.add('description', 'Room Responsible Shift')
#
#             cal.add_component(event)
#             for person in assigned_persons:
#                 person.get_calendar().add_component(event)
#
#
#     with open('OpenhoudenSchedule.ics', 'wb') as file:
#         file.write(cal.to_ical())
#     if not os.path.exists('schedules'):
#         os.makedirs('schedules')
#     for person in PERSONS:
#         with open(f'schedules/Openhouden{person.get_name()}.ics', 'wb') as file:
#             file.write(person.get_calendar().to_ical())
#
#     print("iCalendar files created succesfully")

def line_to_list(line: str): 
    return list(filter(None, line.rstrip().split(",")))

def read_availabilities(csv):
    PERSONS = []
    DATES = []
    SHIFTS = []
    BIN_WEIGHTS = []

    # The amount of cells one Shift takes in the csv file
    SHIFTCSV = 4
    # How many datacolumns each date has (date + is_exam currently) 
    DATEDATA = 2
    # How man rows of information before the dates start 
    DATEDATASTART = 4

    file = csv

    # Read all lines
    for index, line in enumerate(file):
        # Read first line, which are the shifts
        if index == 0:
            shifts = line
            for i in range(int(len(shifts) / SHIFTCSV)):
                SHIFTS.append(Shift(*[shifts[i * SHIFTCSV + j] for j in range(SHIFTCSV)]))
        elif index == 1:
            persons = line[2:]
            for i in range(len(persons)):
                PERSONS.append(Person(persons[i], SHIFTS))
        elif index == 2:
            max_shifts = line[2:]
            for i in range(len(max_shifts)):
                PERSONS[i].set_max_shifts(int(max_shifts[i]) if int(max_shifts[i]) == -1 else int(max_shifts[i]) * 4)
        elif index == 3:
            board = line[2:]
            for i in range(len(board)):
                PERSONS[i].set_board(int(board[i]))
                print(PERSONS[i])
        else:
            data = line
            dt = datetime.strptime(data[0], "%d/%m/%Y")
            DATES.append(Date(exams = int(data[1]), is_monday = dt.weekday() == 0, date = dt, DATES=DATES))

            availabilities = line[DATEDATA:]
            print(availabilities)
            for i in SHIFTS:
                DATES[index - DATEDATASTART].add_shift(copy.deepcopy(i))
                BIN_WEIGHTS.append(i.get_weight())
            for i, v in enumerate(availabilities):
                for j in DATES[index - DATEDATASTART].get_shifts():
                    if j.get_indicator() in v:
                        j.add_available_person(PERSONS[i])
                        PERSONS[i].bin_preference.append(1)
                    else:
                        PERSONS[i].bin_preference.append(0)
    return PERSONS, DATES, SHIFTS, BIN_WEIGHTS

# if __name__ == "__main__":
#     if os.path.isfile(file_name):
#         read_availabilities(file_name)
#
#         solvescip()
#
#         for i in PERSONS:
#             i.assign_from_bin()
#
#         print_results()
