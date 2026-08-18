setting drive_backend        orc_plugins.travel.dal.drive.tomtom
setting flight_backend       orc_plugins.travel.dal.flight.aerodatabox
setting cron                 '0 6 * * *'
setting window_hours         6
setting tomtom_secret        TOMTOM_KEY
setting aerodatabox_secret   AERODATABOX_KEY
setting http_timeout         120
setting buffer_minutes       10

place Home   '123 Main St, Springfield'
place Office '500 Market St, Metropolis'

extra Coffee   10
extra Parking  20
