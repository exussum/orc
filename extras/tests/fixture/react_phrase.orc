phrase HOME  'And(Anyone(), Eq(Light.desk.switch, "on"))'
phrase MUGGY 'And(Phrase.HOME, Entered(dewpoint(self, "temperature", "humidity"), 55, 100))'
react 'Phrased' Sensor.living if 'And(Phrase.MUGGY, Range(Outside.temperature, 66, 120))' set AC cool:low:77
react 'Inline' Sensor.living if 'And(And(And(Anyone(), Eq(Light.desk.switch, "on")), Entered(dewpoint(self, "temperature", "humidity"), 55, 100)), Range(Outside.temperature, 66, 120))' set AC cool:low:77
