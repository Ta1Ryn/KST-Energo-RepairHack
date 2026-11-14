using BackendFork.Contracts;
using BackendFork.Domain;
using BackendFork.Infrastructure;
using static BackendFork.Infrastructure.ApiException;

namespace BackendFork.Services;

public sealed class ReferenceService(IEquipmentRepository repository)
{
    public Task<List<SensorNorm>> NormsAsync(CancellationToken ct) => repository.ReadAsync(s=>s.SensorNorms,ct);
    public Task<SensorNorm> SaveNormAsync(Guid? id,SensorNormRequest request,CancellationToken ct) => repository.WriteAsync(s=>
    {
        Require(request.Min is not null || request.Max is not null,"Нужна хотя бы одна граница нормы.");
        Require(request.Min is null || request.Max is null || request.Min<=request.Max,"Min не может превышать Max.");
        Require(request.WarningMin is null || request.WarningMax is null || request.WarningMin<=request.WarningMax,"Неверные предупредительные границы.");
        Require(request.WarningMin is null || (request.Min is null || request.WarningMin>=request.Min) &&
            (request.Max is null || request.WarningMin<=request.Max),"WarningMin должен находиться внутри границ нормы.");
        Require(request.WarningMax is null || (request.Min is null || request.WarningMax>=request.Min) &&
            (request.Max is null || request.WarningMax<=request.Max),"WarningMax должен находиться внутри границ нормы.");
        DocumentExists(s,request.SourceDocumentId);
        var norm=id is null?new SensorNorm():s.SensorNorms.Find(x=>x.Id==id)??throw new ApiException(404,"Норма не найдена.");
        if(id is not null)Require(norm.SensorKind==request.SensorKind.Trim()&&norm.Unit==request.Unit.Trim(),
            "Для изменения физической величины или единиц создайте новую норму.",409);
        norm.Name=request.Name.Trim();norm.SensorKind=request.SensorKind.Trim();norm.Unit=request.Unit.Trim();
        norm.Min=request.Min;norm.Max=request.Max;norm.WarningMin=request.WarningMin;norm.WarningMax=request.WarningMax;
        norm.EquipmentModel=request.EquipmentModel;norm.ComponentType=request.ComponentType;norm.OperatingMode=request.OperatingMode;
        norm.SourceDocumentId=request.SourceDocumentId;norm.IsDemo=request.IsDemo;
        if(id is null)s.SensorNorms.Add(norm);else norm.Revision++;
        return norm;
    },ct);
    public Task<List<Instruction>> InstructionsAsync(CancellationToken ct)=>repository.ReadAsync(s=>s.Instructions,ct);
    public Task<Instruction> SaveInstructionAsync(Guid? id,InstructionRequest request,CancellationToken ct)=>repository.WriteAsync(s=>
    {
        Require(!string.IsNullOrWhiteSpace(request.Text)||request.DocumentId is not null,"Нужен текст инструкции или файл.");
        DocumentExists(s,request.DocumentId);
        var instruction=id is null?new Instruction():s.Instructions.Find(x=>x.Id==id)??throw new ApiException(404,"Инструкция не найдена.");
        instruction.Title=request.Title.Trim();instruction.Text=request.Text??"";instruction.DocumentId=request.DocumentId;instruction.IsDemo=request.IsDemo;
        if(id is null)s.Instructions.Add(instruction);else instruction.Revision++;
        return instruction;
    },ct);
    public Task<List<RecommendationRule>> RulesAsync(CancellationToken ct)=>repository.ReadAsync(s=>s.RecommendationRules,ct);
    public Task<RecommendationRule> SaveRuleAsync(Guid? id,RecommendationRuleRequest request,CancellationToken ct)=>repository.WriteAsync(s=>
    {
        Require(Enum.IsDefined(request.Trigger),"Неизвестное событие правила.");
        Require(s.Instructions.Any(x=>x.Id==request.InstructionId),"Инструкция не найдена.",404);
        Require(request.DefectCode is null||DefectCatalog.All.Any(x=>x.Code==request.DefectCode),"Неизвестный тип дефекта.");
        var rule=id is null?new RecommendationRule():s.RecommendationRules.Find(x=>x.Id==id)??throw new ApiException(404,"Правило не найдено.");
        rule.Trigger=request.Trigger;rule.EquipmentModel=request.EquipmentModel;rule.ComponentType=request.ComponentType;
        rule.SensorKind=request.SensorKind;rule.DefectCode=request.DefectCode;rule.InstructionId=request.InstructionId;
        rule.Message=request.Message.Trim();rule.Enabled=request.Enabled;
        if(id is null)s.RecommendationRules.Add(rule);
        return rule;
    },ct);
    public Task<Equipment> PassportAsync(Guid equipmentId,PassportRequest request,CancellationToken ct)=>repository.WriteAsync(s=>
    {
        var e=EquipmentService.FindEquipment(s,equipmentId);
        Require(request.Passport.DocumentIds is not null&&request.Passport.Parameters is not null,"Паспорт содержит null вместо списка.");
        foreach(var id in request.Passport.DocumentIds)DocumentExists(s,id);
        DocumentExists(s,request.Nameplate.DocumentId);
        Require(request.Nameplate.ManufactureYear is null or >= 1800 &&
            (request.Nameplate.ManufactureYear is null || request.Nameplate.ManufactureYear<=DateTime.UtcNow.Year),"Неверный год выпуска.");
        Require(request.Nameplate.Model is null||request.Nameplate.Model==e.Model,"Модель на шильдике должна совпадать с карточкой.");
        Require(request.Nameplate.SerialNumber is null||request.Nameplate.SerialNumber==e.SerialNumber,"Серийный номер должен совпадать с карточкой.");
        request.Nameplate.Model??=e.Model;request.Nameplate.SerialNumber??=e.SerialNumber;
        e.Nameplate=request.Nameplate;e.Passport=request.Passport;return e;
    },ct);
    private static void DocumentExists(DatabaseState state,Guid? id)=>
        Require(id is null||state.Documents.Any(x=>x.Id==id),"Документ не найден.",404);
    public static bool NormMatches(SensorNorm norm,Equipment equipment,Component component,Sensor sensor)=>
        norm.SensorKind==sensor.Kind&&norm.Unit==sensor.Unit&&
        (norm.EquipmentModel is null||norm.EquipmentModel==equipment.Model)&&
        (norm.ComponentType is null||norm.ComponentType==component.Type)&&
        (norm.OperatingMode is null||norm.OperatingMode==equipment.Passport.OperatingMode);
}
