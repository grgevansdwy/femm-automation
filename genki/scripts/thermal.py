import math

K_COPPER = 401 # W/mK

# Seri Lee et al., "Constriction/Spreading Resistance Model for Electronics Packaging," 1995.
# and https://www.qats.com/cms/2018/08/13/optimizing-heat-sink-base-spreading-resistance-to-enhance-thermal-performance
def R_spread(A_s):
    A_p = 70e-3 * 70e-3
    t = 3e-3

    a = math.sqrt(A_s / math.pi)
    b = math.sqrt(A_p / math.pi)
    k = K_COPPER
    R_f = 1 # TODO
    h = 1 / (R_f * A_p)
    # l_c = math.pi + b / (math.sqrt(math.pi) * a)
   # 
    # lhs = 1 / (k * a * math.pi)
    # ret = lhs * rhs
    # # breakpoint()
# 
    #_i = h * b / k

    lambda_c = math.pi + 1 / (math.sqrt(math.pi) * epsilon)
    phi_c = (math.tanh(lambda_c * tau) + lambda_c / B_i) / (1 + lambda_c / B_i * math.tanh(lambda_c * tau))

print(R_spread(30e-3 * 30e-3))
print(R_spread(9e-3 * 7.6e-3))9e-3 * 7.6e-3))
print(R_spread(30e-3 * 30e-3))
print(R_spread(9e-3 * 7.6e-3))