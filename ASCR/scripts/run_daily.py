#!/usr/bin/env python3
"""Run daily stock radar pipeline."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from src.main import cmd_run_daily
from argparse import Namespace
cmd_run_daily(Namespace())
