#!/usr/bin/env python3
#
# UpdateS1State - uses the opod file to update the quick look state vectors
# Note this replaces an earlier shell script with similar name.
# It caused problems by not finding the correct file when on a month boundary
# (i.e., for 9-30 it looked for 9-31 rather than 10-01, so it didn't find the file)
#
import utilities as u
from datetime import datetime, timedelta
import sys
from subprocess import call
from subprocess import check_output, STDOUT


def main():
    try:
        parfile = sys.argv[1]
        sensor = None
        if len(sys.argv) >= 3:
            sensor = sys.argv[2]
            if sensor not in ["S1A", "S1B", "S1C", "S1D"]:
                u.myerror(f"invalid sensor {sensor} specified on command line")
        if len(sys.argv) == 6:
            year, month, day = [int(x) for x in sys.argv[3:6]]
        else:
            year = None
    except Exception:
        print('\n Updates state vectors by pulling values from OPOD directory\n')
        print('updateS1State.py parfile [sensor] [year month day]')
        print('\nPart of the s1setup package.')
        call('touch StateError.usageerror', shell=True)
        exit()
    #
    if sensor is None:
        cmd = 'grep sensor ' + parfile
        result = str(check_output([cmd], stderr=STDOUT, shell=True), 'utf-8')
        if result.find('S1A') != -1:
            sensor = 'S1A'
        elif result.find('S1B') != -1:
            sensor = 'S1B'
        elif result.find('S1C') != -1:
            sensor = 'S1C'
        elif result.find('S1D') != -1:
            sensor = 'S1D'
        else:
            call('touch StateError.nosensorfound', shell=True)
            u.myerror('no sensor found')
            exit()
    print(sensor)
    #
    # grep date
    if year is None:
        cmd = 'grep date ' + parfile
        result = str(check_output([cmd], stderr=STDOUT, shell=True), 'utf-8')
        pieces = result.split()
        year, month, day = [int(x) for x in pieces[1:4]]
    #
    date1 = datetime(year, month, day)
    # get day before and after
    dateA = date1 + timedelta(days=-1)
    dateB = date1 + timedelta(days=1)
    dateAstr = dateA.strftime("%4Y%2m%2d")
    dateBstr = dateB.strftime("%4Y%2m%2d")
    print(dateAstr, dateBstr)
    #
    # get opod string - create error file if not found
    #
    try:
        opod = u.dols('ls ' + '/Volumes/insar9/ian/Data/SentinelGreenland/OPOD/' +
                      sensor + '*_OPOD_*_V' + dateAstr + '*_' + dateBstr + '*.EOF')[0]
    except Exception:
        call('touch StateError.couldntfind-' + sensor + 'start_OPOD_start_K' +
             dateAstr + 'start_' + dateBstr + 'star.EOF', shell=True)
        exit()
    print(opod)
    #
    # execute the command
    #
    print('updating state')
    call('S1_OPOD_vec ' + parfile + ' ' + opod, shell=True)
    print('updating state complete')


if __name__ == '__main__':
    main()
