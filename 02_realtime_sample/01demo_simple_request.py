#! /usr/bin/env -S python3 -O
#! /usr/bin/env -S python3

import os
import sys
import lzma
import requests
import time
from parse import parse
import yaml
from evaalapi import statefmt, estfmt

server = "http://127.0.0.1:5000/evaalapi/"
trialname = "onlinedemo"


def do_req (req, n=2):
    r = requests.get(server+trialname+req)
    print("\n==>  GET " + req + " --> " + str(r.status_code))
    if False and r.headers['content-type'].startswith("application/x-xz"):
        l = lzma.decompress(r.content).decode('ascii').splitlines()
    else:
        l = r.text.splitlines()
    if len(l) <= 2*n+1:
        print(r.text + '\n')
    else:
        print('\n'.join(l[:n]
                        + ["   ... ___%d lines omitted___ ...   " % len(l)]
                        + l[-n:] + [""]))
    
    return(r)


def demo (maxw):

    ## First of all, reload
    r = do_req("/reload")

    ## Check initial state
    r = do_req("/state")
    s = parse(statefmt, r.text); print(s.named)

    ## Get first 0.5s worth of data
    time.sleep(maxw)
    r = do_req("/nextdata?horizon=0.5")

    ## Look at remaining time
    time.sleep(maxw)
    r = do_req("/state")
    s = parse(statefmt, r.text); print(s.named)
    
    ## Set estimates
    time.sleep(maxw)
    for pos in range(10):
        r = do_req("/nextdata?position=%.1f,%.1f,%.1f" % (pos+.1, pos+.2, pos+.3))
        time.sleep(maxw)

    ## Get estimates
    r = do_req("/estimates", 3)
    s = parse(estfmt, r.text.splitlines()[-1]); print(s.named)

    ## Get log
    time.sleep(maxw)
    r = do_req("/log", 12)

    ## We finish here
    print("Demo stops here")

################################################################

if __name__ == '__main__':
    
    if len(sys.argv) != 3:
        print("""A demo for the EvAAL API.  Usage is
%s [trial] [server]

if omitted, TRIAL defaults to '%s' and SERVER to %s""" %
              (sys.argv[0], trialname, server))
    else:
        trialname = sys.argv[1]
        server = sys.argv[2]

    print("# Running %s demo test suite\n")
    print(f"trial: {trialname}, server: {server}")
    maxw = 0.5
    demo(maxw)
    exit(0)

