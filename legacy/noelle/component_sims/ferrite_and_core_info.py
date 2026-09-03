from dataclasses import dataclass, fields
import numpy as np
import femm


@dataclass
class toroidal_core_shape():
    name: str
    od: float = 0 #outer diameter in mm
    id: float = 0 #inner diameter in mm
    h: float = 0 #height
    type: str = 'toroid'

    def volume(self) -> float:
        return ((self.od**2)-(self.id**2))*np.pi*self.h ## mm^3

@dataclass
class planar_e_core_shape():
    name: str
    length: float = 0 #outer diameter in mm
    width: float = 0 #inner diameter in mm
    center_leg_width: float = 0 # c - height
    interior_length: float = 0
    slot_depth: float = 0 #
    air_gap_um: float = 0 #air gap in um
    height: float = 0
    type: str = 'E'

    #      
    #           v--------------V--interior length (B/E)         
    #     /   / center width   /  / |
    #    /___/     __v_      /__/  |
    #   |   |     |   |     |  |  /
    # h |   |_____|   |_____|  | / width
    #   |______________________|/
    #           Length

    def cavity_length(self):
        return (self.interior_length-self.center_leg_width)/2
    
    def outer_edge_length(self):
        return (self.length - self.interior_length) / 2

@dataclass
class planar_plate_core():
    name: str
    length: float = 0 #outer diameter in mm
    width: float = 0 #inner diameter in mm
    height: float = 0 
    type: str = 'I'


@dataclass
class ferrite():
    name: str
    mu_i: float #relative permeability
    mu_prime_prime: float=0
    resistivity: float=1e11 # Ohms*cm
    coercivity: float=20 #Oe

    def conductivity(self):
        dimensionally_corrected_resistivity =  self.resistivity/1e2 # Ohms*m
        conductivity =  1/dimensionally_corrected_resistivity #S/m
        return conductivity*1e-6
    
    def loss_delta_angle(self):
        return np.arctan(self.mu_prime_prime / self.mu_i)
    
    def get_mu_freq_dependent(freq):
        #TODO
        return
   
    def get_coercivity_Apm(self): #A/m
        return self.coercivity*79.5774715459


@dataclass
class toroid_core():
    shape: toroidal_core_shape
    material : ferrite

@dataclass
class inductor():
    core: toroid_core
    turns: int
    num_stacked_cores: int




T12 = toroidal_core_shape(name='T12', od=3.18, id=1.57, h=1.27)
T14 = toroidal_core_shape(name='T14', od=3.43, id=1.7, h=1.07)
T16 = toroidal_core_shape(name='T16', od=4.06, id=1.98, h=1.52)
T80 = toroidal_core_shape(name='T80', od=20.19, id=12.57, h=12.7)
T94  = toroidal_core_shape(name='T94' , od=23.93, id=14.22, 	h=7.92)
T106 = toroidal_core_shape(name='T106', od=26.92, id=14.48, 	h=11.1)
T130 = toroidal_core_shape(name='T130', od=33.02, id=19.81, 	h=11.1)
T157 = toroidal_core_shape(name='T157', od=39.88, id=24.13, 	h=14.48)
T184 = toroidal_core_shape(name='T184', od=46.74, id=24.13, 	h=18.03)
T200 = toroidal_core_shape(name='T200', od=50.8,  id=31.75, 	h=13.97)
T225 = toroidal_core_shape(name='T225', od=57.15, id=35.69, 	h=13.97)
T250 = toroidal_core_shape(name='T250', od=63.5,  id=31.75, 	h=25.4)


Ferrox_E14_ungapped = planar_e_core_shape (name='E14', length=14 , interior_length=11 ,center_leg_width=3 , height=3.5 , slot_depth=2 , width=5 , air_gap_um=0)
Ferrox_E14_250u_gap = planar_e_core_shape (name='E14', length=14 , interior_length=11 ,center_leg_width=3 , height=3.5 , slot_depth=2 , width=5 , air_gap_um=250)
E18_ungapped = planar_e_core_shape (name='E18', length=18 , interior_length=14 ,center_leg_width=4 , height=4 , slot_depth=2 , width=10 , air_gap_um=0)
E18_250u_gap = planar_e_core_shape (name='E18', length=18 , interior_length=14 ,center_leg_width=4 , height=4 , slot_depth=2 , width=10 , air_gap_um=250)
E18_1m_gap = planar_e_core_shape (name='E18', length=18 , interior_length=14 ,center_leg_width=4 , height=4 , slot_depth=2 , width=10 , air_gap_um=1000)
E22_ungapped = planar_e_core_shape (name='E18', length=21.8 , interior_length=16.8 ,center_leg_width=5 , height=5.7 , slot_depth=3.2 , width=15.8 , air_gap_um=0)
E64 = planar_e_core_shape (name='E64', length=64 , interior_length=53.8 ,center_leg_width=10.3 , height=10.35 , slot_depth=5.3 , width=51 , air_gap_um=0)
E43 = planar_e_core_shape (name='E43', length=43.2 , interior_length=34.4 ,center_leg_width=8.15 , height=9.55 , slot_depth=5.7 , width=27.9 , air_gap_um=0)

Noel_custom_E = planar_e_core_shape (name='custom', length=30 , interior_length=25 ,center_leg_width=2 , height=10, slot_depth=5 , width=60 , air_gap_um=0)



Ferrox_E58_ungapped = planar_e_core_shape(name='E58', length=58.4 , interior_length=50 ,center_leg_width=8.4 , height=10.5, slot_depth=6.5, width=38.1 , air_gap_um=0)
Ferrox_PLT58 = planar_plate_core(name='PLT58', length=58.4, width=38.1, height=4.1)
Fairite_I14 = planar_plate_core(name='I14', length=14, width=5, height=1.5)
I18 = planar_plate_core(name='I18', length=18, width=10, height=2)
I22 = planar_plate_core(name='I22', length=21.8, width=15.8, height=2.5)
I64 = planar_plate_core(name='I64', length=64, width=51, height=5.08)
I43 = planar_plate_core(name='I43', length=43.2, width=27.9, height=3.85)



#NMG
T1247_NMG   = toroidal_core_shape(name='1-247-X ', od=0.55*25.4, id=0.354*25.4, h=0.197*25.4)
T11270_NMG  = toroidal_core_shape(name='11-270-X', od=0.62*25.4, id=0.350*25.4, h=0.185*25.4)
T663_NMG    = toroidal_core_shape(name='T-663-X ', od=0.87*25.4, id=0.540*25.4, h=0.250*25.4)
T11295_NMG  = toroidal_core_shape(name='11-295-X', od=0.90*25.4, id=0.551*25.4, h=0.276*25.4)
T1015_NMG   = toroidal_core_shape(name='T-1015-X', od=1.00*25.4, id=0.500*25.4, h=0.250*25.4)
T665_NMG    = toroidal_core_shape(name='T-665-X ', od=1.25*25.4, id=0.750*25.4, h=0.375*25.4)
T1100_NMG   = toroidal_core_shape(name='T-1100-X', od=1.39*25.4, id=0.641*25.4, h=0.375*25.4)
T1086_NMG   = toroidal_core_shape(name='T-1086-X', od=1.55*25.4, id=0.965*25.4, h=0.375*25.4)
T1090_NMG   = toroidal_core_shape(name='T-1090-X', od=1.78*25.4, id=0.770*25.4, h=0.375*25.4)
T682_NMG    = toroidal_core_shape(name='T-682-X ', od=1.85*25.4, id=0.450*25.4, h=0.375*25.4)
T020819_NMG = toroidal_core_shape(name='020819-x', od=2.00*25.4, id=1.000*25.4, h=0.300*25.4)
T1807_NMG   = toroidal_core_shape(name='T-1807-X', od=50.8     , id=31.75     , h=12.7      )
M2_T1807_NMG= toroidal_core_shape(name='M2-1807' , od=54.229   , id=33.909    , h=12.7      ) #this is 1807 but with the shrinkage of M2 accounted for

T989_NMG    = toroidal_core_shape(name='T-989-X ', od=2.40*25.4, id=1.400*25.4, h=0.500*25.4)
T1808_NMG   = toroidal_core_shape(name='T-1808-X', od=2.50*25.4, id=0.500*25.4, h=0.750*25.4)
T081963_NMG = toroidal_core_shape(name='081963-X', od=2.55*25.4, id=1.750*25.4, h=0.750*25.4)

Fair_rite_5968003801 = toroidal_core_shape(name='5968003801', od=61, id=35.55, h=12.7)
Fair_rite_5968000301 = toroidal_core_shape(name='5968000301', od=12.7, id=7.15, h=4.775)

Fair_rite_5961004101 = toroidal_core_shape(name='5961004101', od=12.45, id=8, h=6.1)
Fair_rite_5961001121 = toroidal_core_shape(name='5961001121', od=13.45, id=7.2, h=7.1)
Fair_rite_5961001921 = toroidal_core_shape(name='5961001921', od=13.45, id=7.2, h=13.55)
Fair_rite_5961004921 = toroidal_core_shape(name='5961004921', od=16.9, id=8.8, h=7.1)
Fair_rite_5961002721 = toroidal_core_shape(name='5961002721', od=36.8, id=21.95, h=13.7)
Fair_rite_5961001721 = toroidal_core_shape(name='5961001721', od=33, id=18.05, h=10.3)
Fair_rite_5961001221 = toroidal_core_shape(name='5961001221', od=30.15, id=18.0, h=14.64)
Fair_rite_5961001821 = toroidal_core_shape(name='5961001821', od=23, id=12.9, h=7.1)
Fair_rite_5961000621 = toroidal_core_shape(name='5961000621', od=21.85, id=12.4, h=7.1)
Fair_rite_5961000801 = toroidal_core_shape(name='5961000801', od=3.95, id=2.225, h=1.275)
Fair_rite_2661800102 = toroidal_core_shape(name='2661800102', od=5.95, id=3.15, h=1.65)
Fair_rite_2661800202 = toroidal_core_shape(name='2661800202', od=9.70, id=4.9, h=3.3)
Fair_rite_2661800302 = toroidal_core_shape(name='2661800302', od=12.95, id=7.35, h=4.9)
Fair_rite_2661801102 = toroidal_core_shape(name='2661801102', od=12.95, id=8.1, h=6.6)
Fair_rite_2661801802 = toroidal_core_shape(name='2661801802', od=22.5, id=14, h=6.6)
Fair_rite_5961000111 = toroidal_core_shape(name='5961000111', od=5.99, id=2.91, h=1.69)

Fair_rite_5967000301 = toroidal_core_shape(name='5967000301', od=12.7, id=7.15, h=4.775)
Fair_rite_5967001201 = toroidal_core_shape(name='5967001201', od=29, id=19, h=13.85)
Fair_rite_5967000101 = toroidal_core_shape(name='5967000101', od=5.825, id=3.5, h=1.525)
Fair_rite_5967000201 = toroidal_core_shape(name='5967000201', od=9.5, id=4.75, h=3.175)
Fair_rite_5967003821 = toroidal_core_shape(name='5967003821', od=62.8, id=34.2, h=13.7)


#Peak current in Amps -- as pulled from the FFT of the LTspice sim at VPA=114V, 1000W from: https://docs.google.com/spreadsheets/d/1lxfVlpxaQdByefiunx2Ij78ism41yd3a6_JA-fKzQns/edit?gid=2060988589#gid=2060988589
PA_tank_harmonic_currents = [44.25017554, 0.07087857831, 0.7952707288, 0.5017819072, 0.0842393482,
                              0.10977792, 0.03058545868, 0.02067830791, 0.01430589384, 0.02573445634, 0.01189912421,
                                0.0189240897, 0.005827939005, 0.01013712958, 0.006691369275, 0.005963689144, 0.006317059941,
                                  0.003850481116, 0.002757496677, 0.00207379106, 0.00127648289, 0.003940170343, 0.002920888628,
                                    0.002532299084, 0.002514866859, 0.002514866859, 0.0008063340466, 0.002514866859, 0.001664619577,
                                      0.002786217386, 0.002821727026, 0.002821727026, 0.002821727026, 0.002821727026, 0.002821727026,
                                        0.002821727026, 0.002821727026, 0.002821727026, 0.002821727026, 0.002821727026, 0.002821727026,
                                          0.002821727026, 0.002821727026, 0.002821727026, 0.002821727026]

OC_match_inductor_harmonic_currents = [] 

cores = []
# cores.append(T12)
# cores.append(T14)
# cores.append(T16)
# cores.append(T80)
# cores.append(T94)
# cores.append(T106)
# cores.append(T130)
# cores.append(T157)
# cores.append(T184)
# cores.append(T200)
# cores.append(T225)
# cores.append(T250)
# cores.append(T1247_NMG  )
# cores.append(T11270_NMG )
# cores.append(T663_NMG   )
# cores.append(T11295_NMG )
# cores.append(T1015_NMG  )
# cores.append(T665_NMG   )
# # cores.append(T1100_NMG  )
# # cores.append(T1086_NMG  )
# # cores.append(T1090_NMG  )
# # cores.append(T682_NMG   )
# # cores.append(T020819_NMG)
# # cores.append(T1807_NMG  )
# # cores.append(T989_NMG   )
# # cores.append(T1808_NMG  )
# # cores.append(T081963_NMG)
# cores.append(M2_T1807_NMG)

# cores.append(Fair_rite_5968003801)
# cores.append(Fair_rite_5961002721)
# cores.append(Fair_rite_5961001721)
# cores.append(Fair_rite_5961001221)
# cores.append(Fair_rite_5961001821)
# cores.append(Fair_rite_5961000621)
# cores.append(Fair_rite_5961000801)
# cores.append(Fair_rite_2661800102)
# cores.append(Fair_rite_2661800202)
# cores.append(Fair_rite_2661800302)
# cores.append(Fair_rite_2661801102)
# cores.append(Fair_rite_2661801802)
# cores.append(Fair_rite_5961000111)
# cores.append(Fair_rite_5968000301)
# cores.append(Fair_rite_5961004101)
# cores.append(Fair_rite_5961001121)
# cores.append(Fair_rite_5961001921)
# cores.append(Fair_rite_5961004921)

cores.append(Fair_rite_5967000301)
cores.append(Fair_rite_5967001201)
cores.append(Fair_rite_5967000101)
cores.append(Fair_rite_5967000201)
cores.append(Fair_rite_5967003821)


f0 = ferrite(name='_-0', mu_i = 1)
f1 = ferrite(name='_-1', mu_i = 20)
f2 = ferrite(name='_-2', mu_i = 10)
f5 = ferrite(name='_-5', mu_i = 5)
f6 = ferrite(name='_-6', mu_i = 8.5)
f7 = ferrite(name='_-7', mu_i = 9)
f10 = ferrite(name='_-10', mu_i = 6)
f17 = ferrite(name='_-17', mu_i=4)
NMG_M2 = ferrite(name='_M2', mu_i=40, mu_prime_prime=.1, resistivity=1e7)
Fair_rite_68 = ferrite(name='_68', mu_i=16, mu_prime_prime=.12, resistivity=1e7)
Fair_rite_61 = ferrite(name='_61', mu_i=125, mu_prime_prime=.91, resistivity=1e8)
Ferrox_4F1 = ferrite(name='_4F1', mu_i=80, mu_prime_prime=1, resistivity=1e5)
Fair_rite_67 = ferrite(name='_67', mu_i=38.01, mu_prime_prime=.23, resistivity=1e7)
Fair_rite_61_20MHz = ferrite(name='_61_20MHz', mu_i=156.5276, mu_prime_prime=22.54366, resistivity=1e8)
Fair_rite_95 = ferrite(name='FR95', mu_i=3000, mu_prime_prime=15, resistivity=200)

NMG_N40 = ferrite(name='N40', mu_i=15, mu_prime_prime=.07, resistivity=1e10, coercivity=8)




ferrites = []
# ferrites.append(f0 )
# ferrites.append(f1 )
# ferrites.append(f2 )
# ferrites.append(f5 )
# ferrites.append(f6 )
# ferrites.append(f7 )
# ferrites.append(f10)
ferrites.append(Fair_rite_68)
#ferrites.append(f17)
#ferrites.append(NMG_M2)
ferrites.append(Fair_rite_61)
ferrites.append(Fair_rite_67)






def draw_planar_e_core(core, ferrite_name, air_gap_um, height_offset, orientation, fillet_radius):
    #orientation: 1 = facing up, -1 = facing down
    #      
    #           v------------V--interior length (B/E)         
    #     /   / center width /  /|
    #   2/__/3   6__7    10/__/11|
    #   |  |     |  |     |  |  /
    # h |  |4___5|  |8___9|  | / width
    #  1|____________________|/12
    #           Length
    
    offset = core.length/2

    center_fillet_height = core.slot_depth-(air_gap_um/1000)
    
    vertex1 = (-offset, height_offset)
    vertex2 = (-offset, (orientation*core.height)+height_offset)
    vertex3 = (-offset+core.outer_edge_length(), (orientation*core.height)+height_offset)
    vertex4 = (-offset+core.outer_edge_length(), orientation*(core.height-core.slot_depth+fillet_radius)+height_offset)
    vertex4p5 = (-offset+core.outer_edge_length()+fillet_radius, orientation*(core.height-core.slot_depth)+height_offset)
    vertex5 = (-offset+core.outer_edge_length()+core.cavity_length()-center_fillet_height, orientation*(core.height-core.slot_depth)+height_offset)
    vertex5p5 = (-offset+core.outer_edge_length()+core.cavity_length(), orientation*(core.height-core.slot_depth+center_fillet_height)+height_offset)
    vertex6 = (-offset+core.outer_edge_length()+core.cavity_length(), orientation*(core.height-(air_gap_um/1000))+height_offset)
    vertex7 = (-offset+core.outer_edge_length()+core.cavity_length()+core.center_leg_width, orientation*(core.height-(air_gap_um/1000))+height_offset)
    vertex8 = (-offset+core.outer_edge_length()+core.cavity_length()+core.center_leg_width, orientation*(core.height-core.slot_depth+center_fillet_height)+height_offset)
    vertex8p5 = (-offset+core.outer_edge_length()+core.cavity_length()+core.center_leg_width+center_fillet_height, orientation*(core.height-core.slot_depth)+height_offset)
    vertex9 = (-offset+core.outer_edge_length()-fillet_radius+(core.cavity_length()*2)+core.center_leg_width, orientation*(core.height-core.slot_depth)+height_offset)
    vertex9p5 = (-offset+core.outer_edge_length()+(core.cavity_length()*2)+core.center_leg_width, orientation*(core.height-core.slot_depth+fillet_radius)+height_offset)
    vertex10 = (-offset+core.outer_edge_length()+(core.cavity_length()*2)+core.center_leg_width, (orientation*core.height)+height_offset)
    vertex11 = (-offset+(core.outer_edge_length()*2)+(core.cavity_length()*2)+core.center_leg_width, (orientation*core.height)+height_offset)
    vertex12 = (offset, height_offset)


    femm.mi_drawline(*vertex1, *vertex2)
    femm.mi_drawline(*vertex2, *vertex3)
    femm.mi_drawline(*vertex3, *vertex4)
    if(orientation==1):
        femm.mi_drawarc(*vertex4, *vertex4p5, 90, 1)
    elif (orientation==-1):
        femm.mi_drawarc(*vertex4p5, *vertex4, 90, 1)
    femm.mi_drawline(*vertex4p5, *vertex5)
    if (center_fillet_height > 0):
            if(orientation==1):
                femm.mi_drawarc(*vertex5, *vertex5p5, 90, 1)
            if(orientation==-1):
                femm.mi_drawarc(*vertex5p5, *vertex5, 90, 1)
    femm.mi_drawline(*vertex5p5, *vertex6)
    femm.mi_drawline(*vertex6, *vertex7)
    femm.mi_drawline(*vertex7, *vertex8)
    if (center_fillet_height > 0):
        if(orientation==1):
            femm.mi_drawarc(*vertex8, *vertex8p5, 90, 1)
        elif(orientation==-1):
            femm.mi_drawarc(*vertex8p5, *vertex8, 90, 1) 
        
    
    femm.mi_drawline(*vertex8p5, *vertex9)

    if(orientation==1):
        femm.mi_drawarc(*vertex9, *vertex9p5, 90, 1)
    elif(orientation==-1):
        femm.mi_drawarc(*vertex9p5, *vertex9, 90, 1)
    femm.mi_drawline(*vertex9p5, *vertex10)
    femm.mi_drawline(*vertex10, *vertex11)
    femm.mi_drawline(*vertex11, *vertex12)
    femm.mi_drawline(*vertex12, *vertex1)



    femm.mi_addblocklabel(0, (orientation*(core.height-core.slot_depth)/2 + height_offset))
    femm.mi_selectlabel(0, (orientation*(core.height-core.slot_depth)/2 + height_offset))
    femm.mi_setblockprop(ferrite_name, 1, 1, 0)
    femm.mi_clearselected()

    # femm.mi_setblockprop('Copper', 1, 1, 'current', 0, 1, -1)
    # femm.mi_clearselected()

    femm.mi_addblocklabel(0, -1)
    femm.mi_selectlabel(0, -1)
    femm.mi_setblockprop('Air', 1, 1, 0)
    femm.mi_clearselected()

def draw_planar_plate(core, core_plate, ferrite_name, spacing):
    offset = core_plate.length/2
    
    vertex1 = (-offset, spacing+core.height)
    vertex2 = (-offset, spacing+core.height+core_plate.height)
    vertex3 = (offset, spacing+core.height+core_plate.height)
    vertex4 = (offset, spacing+core.height)

    femm.mi_drawline(*vertex1, *vertex2)
    femm.mi_drawline(*vertex2, *vertex3)
    femm.mi_drawline(*vertex3, *vertex4)
    femm.mi_drawline(*vertex4, *vertex1)

    femm.mi_addblocklabel(0, spacing+core.height+(core_plate.height/2))
    femm.mi_selectlabel(0, spacing+core.height+(core_plate.height/2))
    femm.mi_setblockprop(ferrite_name, 1, 1, 0)
    femm.mi_clearselected()


def add_ferrite_to_sim(ferrite):
    femm.mi_addmaterial(ferrite.name)
    femm.mi_modifymaterial(ferrite.name, 1, ferrite.mu_i)
    femm.mi_modifymaterial(ferrite.name, 2, ferrite.mu_i)
    femm.mi_modifymaterial(ferrite.name, 5, ferrite.conductivity())
    femm.mi_modifymaterial(ferrite.name, 10, ferrite.loss_delta_angle())
    femm.mi_modifymaterial(ferrite.name, 11, ferrite.loss_delta_angle())
    femm.mi_modifymaterial(ferrite.name, 3, ferrite.get_coercivity_Apm())
