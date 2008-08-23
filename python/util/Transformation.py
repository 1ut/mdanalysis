# $Id$
from Scientific.Geometry import Transformation

class Transformation:
    def __init__(self):
        pass
    def transform(self):
        raise NotImplemented()

class Recenter(Transformation):
    def __init__(self, system, asel):
        self.system = system
        self.asel = asel
    def transform(self):
        com = self.asel.centerOfMass()
        self.system.coord -= self.asel.centerOfMass()

class RMSOrient(Transformation):
    def __init__(self, system, asel):
        self.system = system
        self.asel = asel
    #def transform(self):
    #    # XXX Not complete yet
    #    com = self.asel.centerOfMass()

