import sys

ADM_OFFSET = 32000
ADM_SLOPE = 9250
IPA_MAX = 8.2
VRECT_BASE = 70

def calculate_max_ipa_crash(vpa, vrect):
    # Translate to firmware variable names
    max_ipa_limit_ratio = ADM_OFFSET / 1e6
    vrect_admittance_scale = ADM_SLOPE / 1e6
    pa_v = vpa
    rectified_v = vrect * 1000
    ipa_soft_limit = IPA_MAX
    admittance_vrect_bonus_start = VRECT_BASE

    # ifaceTX.max_ipa_crash = (ifaceTX.pa_v * ifaceTX.max_ipa_limit_ratio);
    max_ipa_crash = pa_v * max_ipa_limit_ratio

    # ifaceInternalSCI.vrect_ipa_bonus = (((((((ifaceRX.rectified_v / 1000.0f)) - ifaceTX.admittance_vrect_bonus_start)) / 10.0f)) * ((ifaceTX.pa_v * ifaceTX.vrect_admittance_scale)));
    vrect_ipa_bonus = (((((((rectified_v / 1000)) - admittance_vrect_bonus_start)) / 10)) * ((pa_v * vrect_admittance_scale)));

    # ifaceTX.max_ipa_crash = (ifaceInternalSCI.vrect_ipa_bonus) <= (0) ? ifaceTX.max_ipa_crash : ((ifaceTX.max_ipa_crash + ifaceInternalSCI.vrect_ipa_bonus));
    max_ipa_crash = max_ipa_crash if vrect_ipa_bonus <= 0 else (max_ipa_crash + vrect_ipa_bonus)

    # ifaceTX.max_ipa_crash = (ifaceTX.max_ipa_crash) < (ifaceTX.ipa_soft_limit) ? ifaceTX.max_ipa_crash : ifaceTX.ipa_soft_limit;
    max_ipa_crash = max_ipa_crash if (max_ipa_crash < ipa_soft_limit) else ipa_soft_limit

    return max_ipa_crash

if __name__ == '__main__':
    # Operating Point
    vpa = float(sys.argv[1])
    vrect = float(sys.argv[2])
    max_ipa_crash = calculate_max_ipa_crash(vpa, vrect)
    print(max_ipa_crash)
