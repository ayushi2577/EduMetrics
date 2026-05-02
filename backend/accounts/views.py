from rest_framework.permissions import AllowAny
from rest_framework.decorators import permission_classes
from rest_framework.decorators import api_view
from .models import Users
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework.response import Response
from django.db.models import Max
from analysis_engine.models import weekly_metrics

def get_token(user):
    refresh=RefreshToken.for_user(user)
    return{
        'refresh':str(refresh),
        'access':str(refresh.access_token)
    }

@api_view(['POST'])
@permission_classes([AllowAny])
def login(request):
    id=request.data.get('advisor_id')
    password=request.data.get('password')

    if not id :
        return Response({'error':'advisor_id is required'},status=400)
    
    advisor=Users.objects.filter(advisor_id=id).first()
    if advisor:
        actual= f"{advisor.advisor_name}{advisor.advisor_id[-3::]}"

        if password==actual:
            # Get latest semester and week for this advisor's class
            from analysis_engine.models import analysis_state as AnalysisState
            from accounts.models import Users as Advisor
            classXsem={
                    'CSE_Y1_A':1,
                    'CSE_Y2_A':3,
                    'CSE_Y3_A':5,
                    'CSE_Y4_A':7
                }
            try:
                state = AnalysisState.objects.get(id=1)
                max_sem  = state.current_semester
                max_week = state.current_sem_week
                class_id = advisor.class_id
                if max_sem==1:
                    actual_semester = classXsem.get(class_id)
                else:
                    actual_semester = classXsem.get(class_id)+1
            except AnalysisState.DoesNotExist:
                max_sem  = 0
                max_week = 0
                actual_semester = classXsem.get(class_id)

            res = {
                'message':      'Login successful',
                'class_id':     advisor.class_id,
                'advisor_id':   advisor.advisor_id,
                'advisor_name': advisor.advisor_name,
                'semester':     max_sem,
                'sem_week':     max_week,
                'actual_semester': actual_semester
            }
            tokens = get_token(advisor)
            res.update(tokens)
            return Response(res)
        return Response({'error': 'Invalid credentials'}, status=401)
    return Response({'error': 'Advisor not found'}, status=404)
