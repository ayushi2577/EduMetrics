'''
logic is we don't care what ur study methods are if our system thinks that's not impacting ur perf right now and in the longer run (so crammers are a problem for us but not people who don't study as we would like but perform consistently in quizzes,assignments not just exams)
that's why pct change in E_t is observed not just absolute E_t as we believe that we don't know what works for u but we think if u are deviating away from ur methods then that might be a problem and absolute A_t is considered since we just want u to perform well
'''

'''
fetch A_t,E_t,risk_of_detainment,avg A_t over past 2 weeks that meet coverage threshold, E_t over same  time,avg A_t of class over same time, avg E_t of class over same time,number of weeks till next exam,plag pct , risk score over past 2 weeks , streak of missed assignments 
'''

'''
rules:
we think :
priority of risk > priority of struggle > weekly fluctuations
'''


'''
risk score={[a(risk of detainment) +b(pct change in E_t over last 3 weeks) +c(streak of missed assignments) +d(plag pct) + e(lag score)]+f(risk score over past 2 weeks)+g(streak of missed quizzes)}
lag_score=(avg academic perf over past 3 weeks where coverage meets the threshold/avg effort over the same time)/(avg academic perf of class over same time/avg effort of class over same time)
[note : if only week 4,5,7 meet the coverage threshold then academic perf of week 4,5,7 and effort of week 4 to 7 is considered , do remember to exclude exam weeks and initial weeks)
'''

'''
full logic :calc risk score , flag top 20% of risk score
teacher can interpret it as 'these are your top 20% most at risk students'
how do we label risk tier = (risk_score/avg_risk score of the class that week)/weeks left for next exams
but how do we scale risk score like whhich scaling technique to use exactly
'''