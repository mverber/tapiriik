from datetime import timedelta, datetime
from tapiriik.database import cachedb
from tapiriik.database.tz import TZLookup
import hashlib
import pytz


class ActivityType:  # taken from RK API docs. The text values have no meaning except for debugging
    Running = "Running"
    Cycling = "Cycling"
    MountainBiking = "MtnBiking"
    Walking = "Walking"
    Hiking = "Hiking"
    DownhillSkiing = "DownhillSkiing"
    CrossCountrySkiing = "XCSkiing"
    Snowboarding = "Snowboarding"
    Skating = "Skating"
    Swimming = "Swimming"
    Wheelchair = "Wheelchair"
    Rowing = "Rowing"
    Elliptical = "Elliptical"
    Gym = "Gym"
    Other = "Other"

    def List():
        return [ActivityType.Running, ActivityType.Cycling, ActivityType.MountainBiking, ActivityType.Walking, ActivityType.Hiking, ActivityType.DownhillSkiing, ActivityType.CrossCountrySkiing, ActivityType.Snowboarding, ActivityType.Skating, ActivityType.Swimming, ActivityType.Wheelchair, ActivityType.Rowing, ActivityType.Elliptical, ActivityType.Other]

    # The right-most element is the "most specific."
    _hierarchy = [
        [Cycling, MountainBiking],
        [Running, Walking, Hiking]
    ]
    def PickMostSpecific(types):
        types = [x for x in types if x and x is not ActivityType.Other]
        if len(types) == 0:
            return ActivityType.Other
        most_specific = types[0]
        for definition in ActivityType._hierarchy:
            if len([x for x in types if x in definition]) == len(types):
                for act_type in types:
                    if definition.index(most_specific) < definition.index(act_type):
                        most_specific = act_type
        return most_specific

    def AreVariants(types):
        for definition in ActivityType._hierarchy:
            if len([x for x in types if x in definition]) == len(types):
                return True
        return False


class Activity:
    def __init__(self, startTime=None, endTime=None, actType=ActivityType.Other, distance=None, name=None, notes=None, tz=None, lapList=None, private=False, fallbackTz=None, stationary=None, device=None):
        self.StartTime = startTime
        self.EndTime = endTime
        self.Type = actType
        self.Laps = lapList if lapList is not None else []
        self.Stats = ActivityStatistics(distance=distance)
        self.TZ = tz
        self.FallbackTZ = fallbackTz
        self.Name = name
        self.Notes = notes
        self.Private = private
        self.Stationary = stationary
        self.PrerenderedFormats = {}
        self.Device = device

    def CalculateUID(self):
        if not self.StartTime:
            return  # don't even try
        csp = hashlib.new("md5")
        roundedStartTime = self.StartTime
        roundedStartTime = roundedStartTime - timedelta(microseconds=roundedStartTime.microsecond)
        if self.TZ:
            roundedStartTime = roundedStartTime.astimezone(self.TZ)
        csp.update(roundedStartTime.strftime("%Y-%m-%d %H:%M:%S").encode('utf-8'))  # exclude TZ for compat
        self.UID = csp.hexdigest()

    def CountTotalWaypoints(self):
        return sum([len(x.Waypoints) for x in self.Laps])

    def GetFlatWaypoints(self):
        return [wp for waypoints in [x.Waypoints for x in self.Laps] for wp in waypoints]

    def GetFirstWaypointWithLocation(self):
        loc_wp = None
        for lap in self.Laps:
            for wp in lap.Waypoints:
                if wp.Location is not None and wp.Location.Latitude is not None and wp.Location.Longitude is not None:
                    loc_wp = wp.Location
                    break
        return loc_wp

    def DefineTZ(self):
        """ run localize() on all contained dates to tag them with the activity TZ (doesn't change values) """
        if self.TZ is None:
            raise ValueError("TZ not set")
        if self.StartTime and self.StartTime.tzinfo is None:
            self.StartTime = self.TZ.localize(self.StartTime)
        if self.EndTime and self.EndTime.tzinfo is None:
            self.EndTime = self.TZ.localize(self.EndTime)
        for lap in self.Laps:
            lap.StartTime = self.TZ.localize(lap.StartTime) if lap.StartTime.tzinfo is None else lap.StartTime
            lap.EndTime = self.TZ.localize(lap.EndTime) if lap.EndTime.tzinfo is None else lap.EndTime
            for wp in lap.Waypoints:
                if wp.Timestamp.tzinfo is None:
                    wp.Timestamp = self.TZ.localize(wp.Timestamp)
        self.CalculateUID()

    def AdjustTZ(self):
        """ run astimezone() on all contained dates to align them with the activity TZ (requires non-naive DTs) """
        if self.TZ is None:
            raise ValueError("TZ not set")
        self.StartTime = self.StartTime.astimezone(self.TZ)
        self.EndTime = self.EndTime.astimezone(self.TZ)

        for lap in self.Laps:
            lap.StartTime = lap.StartTime.astimezone(self.TZ)
            lap.EndTime = lap.EndTime.astimezone(self.TZ)
            for wp in lap.Waypoints:
                    wp.Timestamp = wp.Timestamp.astimezone(self.TZ)
        self.CalculateUID()

    def CalculateTZ(self, loc=None, recalculate=False):
        if self.TZ and not recalculate:
            return self.TZ
        if loc is None:
            loc = self.GetFirstWaypointWithLocation()
            if loc is None and self.FallbackTZ is None:
                raise Exception("Can't find TZ without a waypoint with a location, or a fallback TZ")
        if loc is None:
            # At this point, we'll resort to the fallback TZ.
            if self.FallbackTZ is None:
                raise Exception("Can't find TZ without a waypoint with a location, specified location, or fallback TZ")
            self.TZ = self.FallbackTZ
            return self.TZ
        # I guess at some point it will be faster to perform a full lookup than digging through this table.
        cachedTzData = cachedb.tz_cache.find_one({"Latitude": loc.Latitude, "Longitude": loc.Longitude})
        if cachedTzData is None:
            res = TZLookup(loc.Latitude, loc.Longitude)
            cachedTzData = {"TZ": res, "Latitude": loc.Latitude, "Longitude": loc.Longitude}
            cachedb.tz_cache.insert(cachedTzData)

        if type(cachedTzData["TZ"]) != str:
            self.TZ = pytz.FixedOffset(cachedTzData["TZ"] * 60)
        else:
            self.TZ = pytz.timezone(cachedTzData["TZ"])
        return self.TZ

    def EnsureTZ(self, recalculate=False):
        self.CalculateTZ(recalculate=recalculate)
        if self.StartTime.tzinfo is None:
            self.DefineTZ()
        else:
            self.AdjustTZ()

    def CheckSanity(self):
        """ Started out as a function that checked to make sure the activity itself is sane.
            Now we perform a lot of checks to make sure all the objects were initialized properly
            I'm undecided on this front...
                - Forcing the .NET model of "XYZCollection"s that enforce integrity seems wrong
                - Enforcing them in constructors makes using the classes a pain
        """
        if "ServiceDataCollection" in self.__dict__:
            srcs = self.ServiceDataCollection  # this is just so I can see the source of the activity in the exception message
        if len(self.Laps) == 0:
                raise ValueError("No laps")
        wptCt = sum([len(x.Waypoints) for x in self.Laps])
        if self.Stationary is None:
            raise ValueError("Activity is undecidedly stationary")
        if not self.Stationary:
            if wptCt == 0:
                raise ValueError("Exactly 0 waypoints")
            if wptCt == 1:
                raise ValueError("Only 1 waypoint")
        if self.Stats.Distance.Value is not None and self.Stats.Distance.asUnits(ActivityStatisticUnit.Meters).Value > 1000 * 1000:
            raise ValueError("Exceedingly long activity (distance)")
        if self.StartTime.replace(tzinfo=None) > (datetime.now() + timedelta(days=5)):
            raise ValueError("Activity is from the future")
        if self.StartTime.replace(tzinfo=None) < datetime(1995, 1, 1):
            raise ValueError("Activity falls implausibly far in the past")
        if self.EndTime and self.EndTime.replace(tzinfo=None) > (datetime.now() + timedelta(days=5 + 5)): # Based on the 5-day activity length limit imposed later.
            raise ValueError("Activity ends in the future")

        if self.StartTime and self.EndTime:
            # We can only do these checks if the activity has both start and end times (Dropbox)
            if (self.EndTime - self.StartTime).total_seconds() < 0:
                raise ValueError("Event finishes before it starts")
            if (self.EndTime - self.StartTime).total_seconds() == 0:
                raise ValueError("0-duration activity")
            if (self.EndTime - self.StartTime).total_seconds() > 60 * 60 * 24 * 5:
                raise ValueError("Exceedingly long activity (time)")

        if len(self.Laps) == 1:
            if self.Laps[0].Stats != self.Stats:
                raise ValueError("Activity with 1 lap has mismatching statistics between activity and lap")
        altLow = None
        altHigh = None
        pointsWithLocation = 0
        unpausedPoints = 0
        for lap in self.Laps:
            if not lap.StartTime:
                raise ValueError("Lap has no start time")
            if not lap.EndTime:
                raise ValueError("Lap has no end time")
            for wp in lap.Waypoints:
                if wp.Type != WaypointType.Pause:
                    unpausedPoints += 1
                if wp.Location:
                    if wp.Location.Latitude == 0 and wp.Location.Longitude == 0:
                        raise ValueError("Invalid lat/lng")
                    if (wp.Location.Latitude is not None and (wp.Location.Latitude > 90 or wp.Location.Latitude < -90)) or (wp.Location.Longitude is not None and (wp.Location.Longitude > 180 or wp.Location.Longitude < -180)):
                        raise ValueError("Out of range lat/lng")
                    if wp.Location.Altitude is not None and (altLow is None or wp.Location.Altitude < altLow):
                        altLow = wp.Location.Altitude
                    if wp.Location.Altitude is not None and (altHigh is None or wp.Location.Altitude > altHigh):
                        altHigh = wp.Location.Altitude
                if wp.Location and wp.Location.Latitude is not None and wp.Location.Longitude is not None:
                    pointsWithLocation += 1
        if unpausedPoints == 1:
            raise ValueError("0 < n <= 1 unpaused points in activity")
        if pointsWithLocation == 1:
            raise ValueError("0 < n <= 1 geographic points in activity") # Make RK happy
        if altLow is not None and altLow == altHigh and altLow == 0:  # some activities have very sporadic altitude data, we'll let it be...
            raise ValueError("Invalid altitudes / no change from " + str(altLow))

    def CleanStats(self):
        """
            Some devices/apps populate fields with patently false values, e.g. HR avg = 1bpm, calories = 0kcal
            So, rather than propagating these, or bailing, we silently strip them, in hopes that destinations will do a better job of calculating them.
            Most of the upper limits match the FIT spec
        """
        def _cleanStatsObj(stats):
            ranges = {
                "Power": [ActivityStatisticUnit.Watts, 0, 5000],
                "Speed": [ActivityStatisticUnit.KilometersPerHour, 0, 150],
                "Elevation": [ActivityStatisticUnit.Meters, -500, 8850], # Props for bringing your Forerunner up Everest
                "HR": [ActivityStatisticUnit.BeatsPerMinute, 15, 300], # Please visit the ER before you email me about these limits
                "Cadence": [ActivityStatisticUnit.RevolutionsPerMinute, 0, 255], # FIT
                "RunCadence": [ActivityStatisticUnit.StepsPerMinute, 0, 255], # FIT
                "Strides": [ActivityStatisticUnit.Strides, 1, 9999999],
                "Temperature": [ActivityStatisticUnit.DegreesCelcius, -62, 50],
                "Energy": [ActivityStatisticUnit.Kilocalories, 1, 65535], # FIT
                "Distance": [ActivityStatisticUnit.Kilometers, 0, 1000] # You can let me know when you ride 1000 km and I'll up this.
            }
            checkFields = ["Average", "Max", "Min", "Value"]
            for key in ranges:
                stat = stats.__dict__[key].asUnits(ranges[key][0])
                for field in checkFields:
                    value = stat.__dict__[field]
                    if value is not None and (value < ranges[key][1] or value > ranges[key][2]):
                        stats.__dict__[key]._samples[field] = 0 # Need to update the original, not the asUnits copy
                        stats.__dict__[key].__dict__[field] = None

        _cleanStatsObj(self.Stats)
        for lap in self.Laps:
            _cleanStatsObj(lap.Stats)

    def CleanWaypoints(self):
        # Similarly, we sometimes get complete nonsense like negative distance
        waypoints = self.GetFlatWaypoints()
        for wp in waypoints:
            if wp.Distance and wp.Distance < 0:
                wp.Distance = 0
            if wp.Speed and wp.Speed < 0:
                wp.Speed = 0
            if wp.Cadence and wp.Cadence < 0:
                wp.Cadence = 0
            if wp.RunCadence and wp.RunCadence < 0:
                wp.RunCadence = 0
            if wp.Power and wp.Power < 0:
                wp.Power = 0
            if wp.Calories and wp.Calories < 0:
                wp.Calories = 0 # Are there any devices that track your caloric intake? Interesting idea...
            if wp.HR and wp.HR < 0:
                wp.HR = 0

    def __str__(self):
        return "Activity (" + self.Type + ") Start " + str(self.StartTime) + " " + str(self.TZ) + " End " + str(self.EndTime) + " stat " + str(self.Stationary)
    __repr__ = __str__

    def __eq__(self, other):
        # might need to fix this for TZs?
        return self.StartTime == other.StartTime and self.EndTime == other.EndTime and self.Type == other.Type and self.Laps == other.Laps and self.Stats.Distance == other.Stats.Distance and self.Name == other.Name

    def __ne__(self, other):
        return not self.__eq__(other)


class UploadedActivity (Activity):
    pass  # will contain list of which service instances contain this activity - not really merited

class LapIntensity:
    Active = 0
    Rest = 1
    Warmup = 2
    Cooldown = 3

class LapTriggerMethod:
    Manual = 0
    Time = 1
    Distance = 2
    PositionStart = 3
    PositionLap = 4
    PositionWaypoint = 5
    PositionMarked = 6
    SessionEnd = 7
    FitnessEquipment = 8

class Lap:
    def __init__(self, startTime=None, endTime=None, intensity=LapIntensity.Active, trigger=LapTriggerMethod.Manual, stats=None, waypointList=None):
        self.StartTime = startTime
        self.EndTime = endTime
        self.Trigger = trigger
        self.Intensity = intensity
        self.Stats = stats if stats else ActivityStatistics()
        self.Waypoints = waypointList if waypointList else []

    def __str__(self):
        return str(self.StartTime) + "-" + str(self.EndTime) + " " + str(self.Intensity) + " (" + str(self.Trigger) + ") " + str(len(self.Waypoints)) + " wps"
    __repr__ = __str__

class ActivityStatistics:
    _statKeyList = ["Distance", "TimerTime", "MovingTime", "Energy", "Speed", "Elevation", "HR", "Cadence", "RunCadence", "Strides", "Temperature", "Power"]
    def __init__(self, distance=None, timer_time=None, moving_time=None, avg_speed=None, max_speed=None, max_elevation=None, min_elevation=None, gained_elevation=None, lost_elevation=None, avg_hr=None, max_hr=None, avg_cadence=None, max_cadence=None, avg_run_cadence=None, max_run_cadence=None, strides=None, min_temp=None, avg_temp=None, max_temp=None, kcal=None, avg_power=None, max_power=None):
        self.Distance = ActivityStatistic(ActivityStatisticUnit.Meters, value=distance)
        self.TimerTime = ActivityStatistic(ActivityStatisticUnit.Seconds, value=timer_time)
        self.MovingTime = ActivityStatistic(ActivityStatisticUnit.Seconds, value=moving_time)
        self.Energy = ActivityStatistic(ActivityStatisticUnit.Kilocalories, value=kcal)
        self.Speed = ActivityStatistic(ActivityStatisticUnit.KilometersPerHour, avg=avg_speed, max=max_speed)
        self.Elevation = ActivityStatistic(ActivityStatisticUnit.Meters, max=max_elevation, min=min_elevation, gain=gained_elevation, loss=lost_elevation)
        self.HR = ActivityStatistic(ActivityStatisticUnit.BeatsPerMinute, avg=avg_hr, max=max_hr)
        self.Cadence = ActivityStatistic(ActivityStatisticUnit.RevolutionsPerMinute, avg=avg_cadence, max=max_cadence)
        self.RunCadence = ActivityStatistic(ActivityStatisticUnit.StepsPerMinute, avg=avg_run_cadence, max=max_run_cadence)
        self.Strides = ActivityStatistic(ActivityStatisticUnit.Strides, value=strides)
        self.Temperature = ActivityStatistic(ActivityStatisticUnit.DegreesCelcius, avg=avg_temp, max=max_temp, min=min_temp)
        self.Power = ActivityStatistic(ActivityStatisticUnit.Watts, avg=avg_power, max=max_power)

    def coalesceWith(self, other_stats):
        for stat in ActivityStatistics._statKeyList:
            self.__dict__[stat].coalesceWith(other_stats.__dict__[stat])
    # Could overload +, but...
    def sumWith(self, other_stats):
        for stat in ActivityStatistics._statKeyList:
            self.__dict__[stat].sumWith(other_stats.__dict__[stat])
    # Magic dict is meh
    def update(self, other_stats):
        for stat in ActivityStatistics._statKeyList:
            self.__dict__[stat].update(other_stats.__dict__[stat])
    def __eq__(self, other):
        if not other:
            return False
        for stat in ActivityStatistics._statKeyList:
            if not self.__dict__[stat] == other.__dict__[stat]:
                return False
        return True

    def __ne__(self, other):
        return not self.__eq__(other)

class ActivityStatistic:
    def __init__(self, units, value=None, avg=None, min=None, max=None, gain=None, loss=None):
        self.Value = value
        self.Average = avg
        self.Min = min
        self.Max = max
        self.Gain = gain
        self.Loss = loss

        # Nothing outside of this class should be accessing _samples (though CleanStats gets a pass)
        self._samples = {}
        self._samples["Value"] = 1 if value is not None else 0
        self._samples["Average"] = 1 if avg is not None else 0
        self._samples["Min"] = 1 if min is not None else 0
        self._samples["Max"] = 1 if max is not None else 0
        self._samples["Gain"] = 1 if gain is not None else 0
        self._samples["Loss"] = 1 if loss is not None else 0

        self.Units = units

    def asUnits(self, units):
        if units == self.Units:
            return self
        newStat = ActivityStatistic(units)
        existing_dict = dict(self.__dict__)
        del existing_dict["Units"]
        del existing_dict["_samples"]
        ActivityStatistic.convertUnitsInDict(existing_dict, self.Units, units)
        newStat.__dict__ = existing_dict
        newStat.Units = units
        newStat._samples = self._samples
        return newStat

    def convertUnitsInDict(values_dict, from_units, to_units):
        for key, value in values_dict.items():
            if value is None:
                continue
            values_dict[key] = ActivityStatistic.convertValue(value, from_units, to_units)

    def convertValue(value, from_units, to_units):
        conversions = {
            (ActivityStatisticUnit.KilometersPerHour, ActivityStatisticUnit.HectometersPerHour): 10,
            (ActivityStatisticUnit.KilometersPerHour, ActivityStatisticUnit.MilesPerHour): 0.621371,
            (ActivityStatisticUnit.MilesPerHour, ActivityStatisticUnit.HundredYardsPerHour): 17.6,
            (ActivityStatisticUnit.MetersPerSecond, ActivityStatisticUnit.KilometersPerHour): 3.6,
            (ActivityStatisticUnit.DegreesCelcius, ActivityStatisticUnit.DegreesFahrenheit): (lambda C: C*9/5 + 32, lambda F: (F-32) * 5/9),
            (ActivityStatisticUnit.Kilometers, ActivityStatisticUnit.Meters): 1000,
            (ActivityStatisticUnit.Meters, ActivityStatisticUnit.Feet): 3.281,
            (ActivityStatisticUnit.Meters, ActivityStatisticUnit.Yards): 1.09361,
            (ActivityStatisticUnit.Miles, ActivityStatisticUnit.Feet): 5280,
            (ActivityStatisticUnit.Kilocalories, ActivityStatisticUnit.Kilojoules): 4.184,
            (ActivityStatisticUnit.StepsPerMinute, ActivityStatisticUnit.DoubledStepsPerMinute): 2
        }
        def recurseFindConversionPath(unit, target, stack):
            assert(unit != target)
            for transform in conversions.keys():
                if unit in transform:
                    if transform in stack:
                        continue  # Prevent circular conversion
                    if target in transform:
                        # We've arrived at the end
                        return stack + [transform]
                    else:
                        next_unit = transform[0] if transform[1] == unit else transform[1]
                        result = recurseFindConversionPath(next_unit, target, stack + [transform])
                        if result:
                            return result
            return None

        conversionPath = recurseFindConversionPath(from_units, to_units, [])
        if not conversionPath:
            raise ValueError("No conversion from %s to %s" % (from_units, to_units))
        for transform in conversionPath:
            if type(conversions[transform]) is float or type(conversions[transform]) is int:
                if from_units == transform[0]:
                    value = value * conversions[transform]
                    from_units = transform[1]
                else:
                    value = value / conversions[transform]
                    from_units = transform[0]
            else:
                if from_units == transform[0]:
                    func = conversions[transform][0] if type(conversions[transform]) is tuple else conversions[transform]
                    value = func(value)
                    from_units = transform[1]
                else:
                    if type(conversions[transform]) is not tuple:
                        raise ValueError("No transform function for %s to %s" % (from_units, to_units))
                    value = conversions[transform][1](value)
                    from_units = transform[0]
        return value

    def coalesceWith(self, stat):
        stat = stat.asUnits(self.Units)

        items = ["Value", "Max", "Min", "Average", "Gain", "Loss"]
        my_items = self.__dict__
        other_items = stat.__dict__
        my_samples = self._samples
        other_samples = stat._samples
        for item in items:
            # Only average if there's a second value
            if other_items[item] is not None:
                # We need to override this so we can be lazy elsewhere and just assign values (.Average = ...) and don't have to use .update(ActivityStatistic(blah, blah, blah))
                other_samples[item] = other_samples[item] if other_samples[item] else 1
                if my_items[item] is None:
                    # We don't have this item's value, nothing to do really.
                    my_items[item] = other_items[item]
                    my_samples[item] = other_samples[item]
                else:
                    my_items[item] += (other_items[item] - my_items[item]) / ((my_samples[item] + 1 / other_samples[item]))
                    my_samples[item] += other_samples[item]

    def sumWith(self, stat):
        """ Used if you want to sum up, for instance, laps' stats to get the activity's stats
            Not all items can be simply summed (min/max), and sum just shouldn't (average)
        """
        stat = stat.asUnits(self.Units)
        summable_items = ["Value", "Gain", "Loss"]
        other_items = stat.__dict__
        for item in summable_items:
            if item in other_items and other_items[item] is not None:
                if self.__dict__[item] is not None:
                    self.__dict__[item] += other_items[item]
                    self._samples[item] = 1 # Break the chain of coalesceWith() calls - this is an entirely fresh "measurement"
                else:
                    self.__dict__[item] = other_items[item]
                    self._samples[item] = stat._samples[item]
        self.Average = None
        self._samples["Average"] = 0

        if self.Max is None or (stat.Max is not None and stat.Max > self.Max):
            self.Max = stat.Max
            self._samples["Max"] = stat._samples["Max"]
        if self.Min is None or (stat.Min is not None and stat.Min < self.Min):
            self.Min = stat.Min
            self._samples["Min"] = stat._samples["Min"]

    def update(self, stat):
        stat = stat.asUnits(self.Units)
        items = ["Value", "Max", "Min", "Average", "Gain", "Loss"]
        other_items = stat.__dict__
        for item in items:
            if item in other_items and other_items[item] is not None:
                self.__dict__[item] = other_items[item]
                self._samples[item] = stat._samples[item]

    def __eq__(self, other):
        if not other:
            return False
        return self.Units == other.Units and self.Value == other.Value and self.Average == other.Average and self.Max == other.Max and self.Min == other.Min and self.Gain == other.Gain and self.Loss == other.Loss

    def __ne__(self, other):
        return not self.__eq__(other)



class ActivityStatisticUnit:
    Seconds = "s"
    Milliseconds = "ms"
    Meters = "m"
    Kilometers = "km"
    Feet = "f"
    Yards = "yd"
    Miles = "mi"
    DegreesCelcius = "ºC"
    DegreesFahrenheit = "ºF"
    KilometersPerHour = "km/h"
    HectometersPerHour = "hm/h" # Silly Garmin Connect!
    MetersPerSecond = "m/s"
    MilesPerHour = "mph"
    HundredYardsPerHour = "hydph" # Hundred instead of Hecto- because imperial :<
    BeatsPerMinute = "BPM"
    RevolutionsPerMinute = "RPM"
    StepsPerMinute = "SPM"
    DoubledStepsPerMinute = "2SPM" # Garmin Connect is still weird.
    Strides = "strides"
    Kilocalories = "kcal"
    Kilojoules = "kj"
    Watts = "W"


class WaypointType:
    Start = 0   # Start of activity
    Regular = 1 # Normal
    Pause = 11  # All waypoints within a paused period should have this type
    Resume = 12 # The first waypoint after a paused period
    End = 100   # End of activity

class Waypoint:
    __slots__ = ["Timestamp", "Location", "HR", "Calories", "Power", "Temp", "Cadence", "RunCadence", "Type", "Distance", "Speed"]
    def __init__(self, timestamp=None, ptType=WaypointType.Regular, location=None, hr=None, power=None, calories=None, cadence=None, runCadence=None, temp=None, distance=None, speed=None):
        self.Timestamp = timestamp
        self.Location = location
        self.HR = hr # BPM
        self.Calories = calories # kcal
        self.Power = power  # Watts. I doubt there will ever be more parameters than this in terms of interchange
        self.Temp = temp  # degrees C. never say never
        self.Cadence = cadence  # RPM. dammit this better be the last one
        self.RunCadence = runCadence  # SPM. screw it
        self.Distance = distance # meters. I don't even care any more.
        self.Speed = speed # m/sec. neghhhhh
        self.Type = ptType

    def __eq__(self, other):
        return self.Timestamp == other.Timestamp and self.Location == other.Location and self.HR == other.HR and self.Calories == other.Calories and self.Temp == other.Temp and self.Cadence == other.Cadence and self.Type == other.Type and self.Power == other.Power and self.RunCadence == other.RunCadence and self.Distance == other.Distance and self.Speed == other.Speed

    def __ne__(self, other):
        return not self.__eq__(other)

    def __str__(self):
        return str(self.Type) + "@" + str(self.Timestamp) + " " + ((str(self.Location.Latitude) + "|" + str(self.Location.Longitude) + "^" + str(round(self.Location.Altitude) if self.Location.Altitude is not None else None)) if self.Location is not None else "") + "\n\tHR " + str(self.HR) + " CAD " + str(self.Cadence) + " RCAD " + str(self.RunCadence) + " TEMP " + str(self.Temp) + " PWR " + str(self.Power) + " CAL " + str(self.Calories) + " SPD " + str(self.Speed) + " DST " + str(self.Distance)
    __repr__ = __str__


class Location:
    __slots__ = ["Latitude", "Longitude", "Altitude"]
    def __init__(self, lat=None, lon=None, alt=None):
        self.Latitude = lat
        self.Longitude = lon
        self.Altitude = alt

    def __eq__(self, other):
        if not other:
            return False
        return self.Latitude == other.Latitude and self.Longitude == other.Longitude and self.Altitude == other.Altitude

    def __ne__(self, other):
        return not self.__eq__(other)
