@echo off
REM OpenBudget monitoringi - bir marta ishlaydi va chiqadi.
REM Windows Task Scheduler har 30 daqiqada shuni chaqiradi.
cd /d "%~dp0"
"C:\Users\Lutfullo\AppData\Local\Python\pythoncore-3.14-64\pythonw.exe" bot.py --once
