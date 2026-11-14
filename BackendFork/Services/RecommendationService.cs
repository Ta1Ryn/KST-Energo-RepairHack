using BackendFork.Contracts;
using BackendFork.Domain;

namespace BackendFork.Services;

public static class RecommendationService
{
    public static List<Recommendation> Build(DatabaseState state,Equipment equipment,Component component,
        ResourceAssessment resource,List<SensorAssessment> sensors,Analysis? visual)
    {
        var result=new List<Recommendation>();
        void Add(RecommendationTrigger trigger,string reason,string fallback,string? sensorKind=null,string? defectCode=null,bool demo=false)
        {
            var rules=state.RecommendationRules.Where(r=>r.Enabled&&r.Trigger==trigger&&
                (r.EquipmentModel is null||r.EquipmentModel==equipment.Model)&&
                (r.ComponentType is null||r.ComponentType==component.Type)&&
                (r.SensorKind is null||r.SensorKind==sensorKind)&&
                (r.DefectCode is null||r.DefectCode==defectCode)).ToList();
            foreach(var rule in rules)
            {
                var instruction=state.Instructions.Find(i=>i.Id==rule.InstructionId);
                if(instruction is not null)result.Add(new(reason,rule.Message,instruction.Id,instruction.Revision,demo||instruction.IsDemo));
            }
            if(rules.Count==0)result.Add(new(reason,fallback,null,null,demo));
        }
        foreach(var sensor in sensors.Where(s=>s.Status is SensorStatus.OutOfRange or SensorStatus.Attention))
        {
            var definition=component.Sensors.Find(x=>x.Key==sensor.Key)!;
            var norm=state.SensorNorms.Find(x=>x.Id==sensor.NormId);
            var reason=$"{sensor.Name}: {sensor.Value} {sensor.Unit}; допустимо {sensor.Min}–{sensor.Max}. Норма {sensor.NormId}, версия {sensor.NormRevision}.";
            Add(sensor.Status==SensorStatus.OutOfRange?RecommendationTrigger.SensorOutOfRange:RecommendationTrigger.SensorAttention,
                reason,"Проверить показание датчика и узел по регламенту.",definition.Kind,demo:norm?.IsDemo??false);
        }
        if(resource.LifetimeStatus is ResourceStatus.Attention or ResourceStatus.Exhausted)
            Add(resource.LifetimeStatus==ResourceStatus.Exhausted?RecommendationTrigger.ResourceExhausted:RecommendationTrigger.ResourceAttention,
                $"Остаток нормативного ресурса: {resource.RemainingHours} ч.","Запланировать проверку ресурса и обслуживания.");
        if(resource.ServiceStatus==ResourceStatus.Exhausted)
            Add(RecommendationTrigger.ServiceDue,"Наступил срок обслуживания по наработке.","Провести обслуживание по утверждённой инструкции.");
        if(visual is not null&&visual.VisualStatus!=VisualStatus.NoVisibleProblems)
        {
            var codes=visual.Detections.Select(d=>DefectCatalog.Code(d.Label)).Where(c=>c is not null).Distinct().ToList();
            if(codes.Count==0)Add(RecommendationTrigger.VisualDefect,visual.Summary,visual.Recommendation);
            else foreach(var code in codes)Add(RecommendationTrigger.VisualDefect,visual.Summary,visual.Recommendation,defectCode:code);
        }
        return result;
    }
}
