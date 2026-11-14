using System.Security.Cryptography;
using BackendFork.Contracts;
using BackendFork.Domain;
using BackendFork.Infrastructure;
using Microsoft.Extensions.Options;
using static BackendFork.Infrastructure.ApiException;

namespace BackendFork.Services;

public sealed class DocumentService(IEquipmentRepository repository,IOptions<BackendOptions> options,IWebHostEnvironment environment,TimeProvider clock)
{
    private string Folder=>Path.GetFullPath(Path.Combine(environment.ContentRootPath,options.Value.DataDirectory,"documents"));
    public string PathFor(StoredDocument document)=>Path.Combine(Folder,document.FileName);
    public Task<StoredDocument> GetAsync(Guid id,CancellationToken ct)=>repository.ReadAsync(s=>
        s.Documents.Find(d=>d.Id==id)??throw new ApiException(404,"Документ не найден."),ct);
    public Task<List<DocumentResponse>> ListAsync(Guid? equipmentId,CancellationToken ct)=>repository.ReadAsync(s=>
        s.Documents.Where(d=>equipmentId is null||d.EquipmentId==equipmentId).Select(Response).ToList(),ct);
    public static DocumentResponse Response(StoredDocument d)=>new(d.Id,d.EquipmentId,d.Category,d.OriginalName,d.ContentType,d.Size,d.Sha256,d.UploadedAt,"/api/documents/"+d.Id);
    public async Task<DocumentResponse> UploadAsync(IFormFile file,Guid? equipmentId,DocumentCategory category,string authorId,CancellationToken ct)
    {
        Require(Enum.IsDefined(category),"Неизвестная категория документа.");
        Require(file.Length>0&&file.Length<=10*1024*1024,"Максимум 10 МБ.",413);
        if(equipmentId is{} id)await repository.ReadAsync(s=>EquipmentService.FindEquipment(s,id),ct);
        using var memory=new MemoryStream();await using(var input=file.OpenReadStream())
        {
            var buffer=new byte[81920];int read;
            while((read=await input.ReadAsync(buffer,ct))>0){Require(memory.Length+read<=10*1024*1024,"Файл слишком большой.",413);memory.Write(buffer,0,read);}
        }
        var bytes=memory.ToArray();
        var format=Format(bytes)??throw new ApiException(415,"Документ должен быть PDF, PNG или JPEG.");
        var document=new StoredDocument { EquipmentId=equipmentId,Category=category,OriginalName=Path.GetFileName(file.FileName),
            FileName=Guid.NewGuid().ToString("N")+format.Extension,ContentType=format.ContentType,
            Size=bytes.Length,Sha256=Convert.ToHexString(SHA256.HashData(bytes)),AuthorId=authorId,UploadedAt=clock.GetUtcNow() };
        Directory.CreateDirectory(Folder);var target=PathFor(document);
        try
        {
            await File.WriteAllBytesAsync(target,bytes,ct);
            await repository.WriteAsync(s=>{s.Documents.Add(document);return true;},ct);
            return Response(document);
        }
        catch{if(File.Exists(target))File.Delete(target);throw;}
    }
    private static (string Extension,string ContentType)? Format(byte[] bytes)
    {
        if(bytes.AsSpan().StartsWith("%PDF-"u8))return(".pdf","application/pdf");
        if(bytes.Length>=8&&bytes.AsSpan(0,8).SequenceEqual(new byte[]{137,80,78,71,13,10,26,10}))return(".png","image/png");
        if(bytes.Length>=3&&bytes[0]==255&&bytes[1]==216&&bytes[2]==255)return(".jpg","image/jpeg");
        return null;
    }
}
