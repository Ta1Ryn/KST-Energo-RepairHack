using BackendFork.Infrastructure;
using Microsoft.AspNetCore.Mvc.Filters;

namespace BackendFork.Workforce;

public sealed class WorkforceAccessFilter(WorkforceDb db):IAsyncActionFilter
{
    public async Task OnActionExecutionAsync(ActionExecutingContext context,ActionExecutionDelegate next)
    {
        var path=context.HttpContext.Request.Path.Value??"";
        if(!db.Enabled || !path.StartsWith("/api/",StringComparison.OrdinalIgnoreCase) || path.StartsWith("/api/workforce") || path.StartsWith("/api/admin") || path=="/api/auth/status")
        {await next();return;}
        if(path.StartsWith("/api/auth"))throw new ApiException(409,"Используйте серверный вход /api/workforce/login.");
        var actor=WorkforceDb.Actor(context.HttpContext.User);var ct=context.HttpContext.RequestAborted;
        var write=!HttpMethods.IsGet(context.HttpContext.Request.Method);
        await using var c=await db.Open(ct);
        var controller=context.Controller.GetType().Name;
        if(write && (controller is "EquipmentController" or "ReferenceController" or "DocumentsController"))
        {
            await using var t=await c.BeginTransactionAsync(ct);
            await WorkforceDb.GlobalAdmin(c,t,actor,"accounts.manage",ct);await t.CommitAsync(ct);
        }
        else
        {
            var permission=controller switch
            {
                "InspectionsController" when write=>"inspections.create",
                "PhotosController" when write=>"inspections.create",
                "AnalysesController" when write=>"inspections.create",
                "EquipmentController" when context.ActionArguments.ContainsKey("componentId")=>"components.read",
                _=>"equipment.read"
            };
            await WorkforceDb.Permission(c,null,actor,permission,ct);
            if(controller=="InspectionsController" && write && context.ActionArguments.Values.OfType<BackendFork.Contracts.InspectionRequest>().FirstOrDefault() is {} inspection)
            {
                if(inspection.Items.Any(x=>x.Action==BackendFork.Domain.WorkAction.DefectRecorded))await WorkforceDb.Permission(c,null,actor,"findings.create",ct);
                if(inspection.Items.Any(x=>x.Action is BackendFork.Domain.WorkAction.Serviced or BackendFork.Domain.WorkAction.Replaced))await WorkforceDb.Permission(c,null,actor,"maintenance.create",ct);
            }
        }
        await next();
    }
}
