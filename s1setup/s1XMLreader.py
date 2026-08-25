#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Read a Sentinel-1 SAFE annotation/calibration XML.

A copy of myDev.S1XMLheader, under a different name so that anything still
importing myDev keeps working unchanged.  It lives here because radcalcoeffs
needs it and myDev is a loose directory in ~/PycharmProjects rather than an
installed package: it is importable only when PYTHONPATH happens to include
that directory, which an interactive shell sets and cron does not.  That made
radcalcoeffs fail with ModuleNotFoundError the first time the assemble stage
ran outside an interactive shell.

Part of the s1setup package.
"""
import os
from bs4 import BeautifulSoup
import utilities as u


class S1XMLreader:

    def __init__(self, fileName):
        ''' Initialize by loading load the xml data'''
        # check file exists and open
        if os.path.exists(fileName):
            fp = open(fileName, 'r')
            self.contents = fp.read()
            self.soup = BeautifulSoup(self.contents, 'xml')
        else:
            u.myerror("S1XMLreader File {0:s} does not exist".format(fileName))

    def drillDown(self, mySoup, myList):
        toFind = myList[0]
        results = mySoup.find_all(toFind)
        #
        if len(myList) == 1:
            return results
        else:
            toReturn = []
            for result in results:
                toReturn.append(self.drillDown(result, myList[1:]))
            return toReturn

    def parseData(self, myList):
        ''' First call in recursion to start with top level '''
        return self.drillDown(self.soup, myList)

    def slantRangeTime(self):
        ''' get the slant range start time '''
        textStr = self.parseData(['imageAnnotation', 'imageInformation',
                                  'slantRangeTime'])
        if len(textStr) == 0:
            u.myerror('S1XMLreader-slantRangeTime - no key found')
        # print(textStr[0][0][0].get_text())
        return float(textStr[0][0][0].get_text())

    def betaNought(self):
        ''' Note there is a lot of redundancy here just to check the beta
        noughts are all the same'''
        textStrs = self.parseData(['calibration', 'calibrationVectorList',
                                   'betaNought'])
        betaNoughts = []
        for textStr in textStrs[0][0]:
            coeffs = [float(x) for x in textStr.get_text().split()]
            if coeffs[0] == coeffs[-1]:
                betaNoughts.append(coeffs[0])
            else:
                u.myerror(
                    'S1XMLreader.betaNought - inconsistent beta nought values')
        if betaNoughts[0] == betaNoughts[-1]:
            return betaNoughts[0]
        else:
            u.myerror('S1XMLreader.betaNought - inconsistent beta nought '
                      'values for last value')
